
"""
Module: bot
Description: A Discord API integration (Discord Bot) for the Manga Downloader.
It allows users to trigger downloads via Discord commands (e.g., !descargar <url>).
The bot features live-updating embed messages for progress logs and automatically
uploads files exceeding Discord's attachment limit to GoFile (see bot_gofile.py).

Translation (MangaScan AI, also on the NAS):
  - "Traducir al español" button under every downloaded PDF.
  - !traducir <GoFile link>  -> translates the LOCAL copy of a file this bot uploaded.
  - !traducir + attached PDF -> for small PDFs from elsewhere.
The PDF goes to MangaScan over the LAN (no size limit); GoFile is only used to
deliver the translated result when it exceeds Discord's attachment limit.
"""
import discord
from discord.ext import commands
import os
import asyncio
import re
import shutil
import aiohttp
import io
import tempfile
import threading
import time
import traceback
from typing import Optional, Set
from dotenv import load_dotenv

import core
import core.config
import mangascan_client
from core import busqueda
from core import agrupador
from core.sites import bunkr as bunkr_site
from core.handler import obra_desde_enlace, host_de_url, dominio_coincide
from core.sites.twitter import DOMINIOS as DOMINIOS_TWITTER
from core.utils import limpiar_temporales_huerfanos
from bot_gofile import _entregar_pdf, _pdf_de_gofile, pdf_traducible, registrar_pdf_traducible
from urllib.parse import urlparse

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# Configure core for Bot Mode (Silent)
core.config.OPEN_RESULT_ON_FINISH = False

MAX_DESCARGAS_SIMULTANEAS = 3
MAX_TRADUCCIONES_SIMULTANEAS = 2  # MangaScan ya encola; esto evita subir 10 a la vez
VISTA_TIMEOUT_SEG = 1800  # menús y botones de búsqueda/continuación: 30 min
LIMPIEZA_TEMPORALES_HORAS = 6

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

download_semaphore = asyncio.Semaphore(MAX_DESCARGAS_SIMULTANEAS)
traduccion_semaphore = asyncio.Semaphore(MAX_TRADUCCIONES_SIMULTANEAS)

# Tareas en segundo plano: se guarda una referencia para que el recolector no las
# destruya a medias y para registrar sus excepciones (create_task suelto las pierde).
_tareas: Set[asyncio.Task] = set()


def spawn(coro, canal=None) -> asyncio.Task:
    """Lanza una corrutina en segundo plano; si falla, lo registra y avisa en `canal` (si se da)."""
    tarea = bot.loop.create_task(coro)
    _tareas.add(tarea)

    def _al_terminar(t: asyncio.Task):
        _tareas.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is None:
            return
        print(f"[ERROR] Tarea en segundo plano: {exc!r}")
        traceback.print_exception(type(exc), exc, exc.__traceback__)
        if canal is not None:
            async def _avisar():
                try:
                    await canal.send(f"[ERROR] Algo falló: {exc}"[:1900])
                except discord.errors.HTTPException:
                    pass
            aviso = bot.loop.create_task(_avisar())
            _tareas.add(aviso)
            aviso.add_done_callback(_tareas.discard)

    tarea.add_done_callback(_al_terminar)
    return tarea


def _pdf_root() -> str:
    return os.path.join(os.getcwd(), core.config.PDF_FOLDER_NAME)


# Búsqueda por nombre en PDF/ (solo !traducir <nombre.pdf> y logs antiguos sin ruta).
# Se llama desde el hilo del PDF y desde el event loop: el Lock evita recorridos simultáneos.
_pdf_busqueda_lock = threading.Lock()


def _find_pdf_fast(filename: str) -> Optional[str]:
    """Busca un archivo por nombre dentro de PDF/. Bloqueante: desde async, usar asyncio.to_thread."""
    filename = os.path.basename(filename)
    if not filename:
        return None
    with _pdf_busqueda_lock:
        raiz = _pdf_root()
        if not os.path.isdir(raiz):
            return None
        for root, dirs, files in os.walk(raiz):
            if filename in files:
                return os.path.join(root, filename)
    return None


def _resolver_generado(ruta_log: str) -> Optional[str]:
    """Ruta real del archivo que anuncia el core: absoluta, relativa a PDF/ o (último recurso) por nombre."""
    if os.path.isabs(ruta_log):
        return ruta_log if os.path.exists(ruta_log) else None
    candidata = os.path.normpath(os.path.join(_pdf_root(), ruta_log))
    if os.path.exists(candidata):
        return candidata
    encontrada = _find_pdf_fast(ruta_log)
    if encontrada:
        return encontrada
    return os.path.abspath(ruta_log) if os.path.exists(ruta_log) else None


def _permitido(ctx, interaction) -> bool:
    """Solo quien lanzó el comando (o un moderador) usa los botones y menús de su mensaje."""
    permisos = getattr(interaction.user, "guild_permissions", None)
    return interaction.user.id == ctx.author.id or bool(permisos and permisos.manage_messages)

class DiscordLogAdapter:
    """
    Muestra el progreso de una descarga en un embed limpio que se va editando
    (mismo estilo que la traducción): título, fase, barra, páginas, velocidad y
    tiempo restante. Detecta los PDF generados en los logs del core para subirlos.
    """
    def __init__(self, ctx, url: str = ""):
        self.ctx = ctx
        self.url = url
        self.message = None
        self.logs = []
        self.generated_files = []
        self.titulo = None
        self.fase = "Preparando..."
        self.actual = 0
        self.total = 0
        self.inicio = time.time()
        self.inicio_descarga = None
        self.reintentos = 0      # el servidor pidió esperar (503/429) y se repitió: normal en Hitomi
        self.paginas_perdidas = 0  # páginas que fallaron tras todos los reintentos
        # Vídeos (leaks): mientras haya uno en curso, la barra muestra su % en vez de páginas
        self.video_pct = None
        self.video_indice = 0
        self.video_total = 0
        self.video_nombre = ""
        self._ultima_edicion = 0.0
        self._ultima_fase = ""
        self.cancelado = False

    def check_cancel(self) -> bool:
        return self.cancelado

    async def _pedir_cancelacion(self):
        self.cancelado = True
        self.fase = "Cancelando (se detiene tras las imágenes en curso)..."
        await self._editar(True)

    async def initialize(self):
        self.vista = CancelarView(self.ctx.author.id, self._pedir_cancelacion)
        self.message = await self.ctx.send(embed=self._embed(), view=self.vista)

    # --- Construcción del embed ---------------------------------------------
    def _embed(self, color=None, titulo=None) -> discord.Embed:
        nombre = self.titulo or "manga"
        embed = discord.Embed(
            title=(titulo or f"📥 Descargando: {nombre}")[:250],
            color=color or discord.Color.blue(),
        )
        lineas = [f"**{self.fase}**"]
        if self.video_pct is not None:
            pct = max(0, min(100, self.video_pct))
            lineas.append(f"`{_barra(pct, 100)}` {pct}%")
            if self.video_total:
                cual = f"vídeo {self.video_indice}/{self.video_total}" if self.video_indice else \
                    f"{self.video_total} vídeo(s)"
                lineas.append(f"{cual} · {self.video_nombre}" if self.video_nombre else cual)
        elif self.total:
            lineas.append(f"`{_barra(self.actual, self.total)}` {self.actual}/{self.total} páginas")
            if self.inicio_descarga and self.actual and self.actual < self.total:
                velocidad = self.actual / max(0.1, time.time() - self.inicio_descarga)
                restante = (self.total - self.actual) / max(velocidad, 0.01)
                lineas.append(f"{velocidad:.1f} páginas/s · quedan ~{_fmt_tiempo(restante)}")
        if self.reintentos:
            lineas.append(f"🔁 {self.reintentos} reintentos automáticos (el servidor limita la velocidad)")
        if self.paginas_perdidas:
            lineas.append(f"⚠️ {self.paginas_perdidas} página(s) no se pudieron descargar")
        embed.description = "\n".join(lineas)
        embed.set_footer(text=f"Pedido por {self.ctx.author.display_name}")
        return embed

    async def _editar(self, forzar: bool = False, quitar_boton: bool = False, **kw):
        if not self.message:
            return
        ahora = time.time()
        if not forzar and self.fase == self._ultima_fase and ahora - self._ultima_edicion < 3:
            return  # no saturar la API de Discord
        self._ultima_edicion, self._ultima_fase = ahora, self.fase
        try:
            if quitar_boton:
                await self.message.edit(embed=self._embed(**kw), view=None)
            else:
                await self.message.edit(embed=self._embed(**kw))
        except discord.errors.HTTPException:
            pass

    async def cancelada(self):
        self.fase = f"Cancelada por el usuario tras {_fmt_tiempo(time.time() - self.inicio)}"
        await self._editar(True, quitar_boton=True, color=discord.Color.dark_grey(),
                           titulo=f"⏹️ Descarga cancelada: {self.titulo or 'manga'}")

    def _programar(self, forzar: bool = False):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is bot.loop:
            spawn(self._editar(forzar))
        else:  # llamado desde un hilo (p. ej. creación del PDF)
            asyncio.run_coroutine_threadsafe(self._editar(forzar), bot.loop)

    # --- Callbacks que usa el core -----------------------------------------
    def progress_callback(self, current: int, total: int):
        if self.inicio_descarga is None:
            self.inicio_descarga = time.time()
        self.actual, self.total = current, total
        self.fase = "Descargando páginas..."
        self._programar()

    def log_callback(self, text: str):
        print(f"[INTERNAL LOG] {text}")
        self.logs = (self.logs + [text])[-10:]
        forzar = False

        if m := re.search(r"Title detected: (.+)", text):
            self.titulo = m.group(1).strip()
            forzar = True
        elif m := re.search(r"Images detected: (\d+)", text):
            n = int(m.group(1))
            if n and n < 9999:
                self.total = n
            self.video_pct = None  # vuelve la fase de páginas (el PDF va antes que los vídeos)
            self.fase = "Descargando páginas..."
            forzar = True
        elif "Generating PDF" in text:
            self.video_pct = None
            self.fase = "Generando el PDF..."
            if self.total:
                self.actual = self.total
            forzar = True
        elif "[INIT]" in text or "Opening reader" in text:
            self.fase = "Abriendo la página..."
        elif "Retrying" in text:
            self.reintentos += 1
        elif m := re.search(r"Vídeo (\d+)/(\d+): (.+)", text):
            self.video_indice, self.video_total = int(m.group(1)), int(m.group(2))
            self.video_nombre = m.group(3).strip()[:80]
            self.video_pct = 0
            self.fase = f"Descargando vídeo {self.video_indice}/{self.video_total}..."
            forzar = True
        elif m := re.search(r"Descargando vídeo: (\d+)%", text):
            self.video_pct = int(m.group(1))
            if self.video_total:
                self.fase = (f"Descargando vídeo {self.video_indice}/{self.video_total}... "
                             f"{self.video_pct}%")
            else:
                self.fase = f"Descargando vídeo... {self.video_pct}%"
        elif m := re.search(r"(\d+) de \d+ páginas no se pudieron descargar", text):
            self.paginas_perdidas = int(m.group(1))

        # Archivo generado: "[SUCCESS] PDF Generated: file.pdf" o "[SUCCESS] File Generated: video.mp4"
        if m := re.search(r"\[SUCCESS\] (?:PDF|File) Generated: (.*)", text):
            ruta = _resolver_generado(m.group(1).strip())
            if ruta:
                self.generated_files.append(ruta)

        self._programar(forzar)

    async def finalizar(self, ok: bool):
        duracion = _fmt_tiempo(time.time() - self.inicio)
        if ok:
            tam = sum(os.path.getsize(f) for f in self.generated_files if os.path.exists(f)) / (1024 * 1024)
            pdfs = sum(1 for f in self.generated_files if f.lower().endswith(".pdf"))
            videos = len(self.generated_files) - pdfs
            if videos:  # leaks: interesa cuántos vídeos, no las "páginas" del PDF de fotos
                resumen = (f"{pdfs} PDF · " if pdfs else "") + f"{videos} vídeo(s)"
                self.fase = f"Listo en {duracion} · {resumen} · {tam:.1f} MB"
            elif self.total:
                completas = (self.total or 0) - self.paginas_perdidas
                self.fase = f"Listo en {duracion} · {completas}/{self.total} páginas · {tam:.1f} MB"
            else:
                self.fase = f"Listo en {duracion} · {tam:.1f} MB"
            self.video_pct = None
            self.actual = self.total
            self.reintentos = 0  # ya no importan: todo llegó (o se avisa de lo que falta)
            color = discord.Color.green() if not self.paginas_perdidas else discord.Color.orange()
            icono = "✅" if not self.paginas_perdidas else "⚠️"
            await self._editar(True, quitar_boton=True, color=color, titulo=f"{icono} Descargado: {self.titulo or 'manga'}")
        else:
            detalle = "\n".join(l for l in self.logs if "[ERROR]" in l or "[WARN]" in l)[-900:]
            self.fase = f"No se generó ningún archivo ({duracion})"
            await self._editar(True, quitar_boton=True, color=discord.Color.red(),
                               titulo=f"❌ Falló la descarga: {self.titulo or self.url}"[:250])
            if detalle:
                emb = self.message.embeds[0] if self.message and self.message.embeds else None
                if emb is not None:
                    emb.add_field(name="Detalle", value=f"```\n{detalle}\n```", inline=False)
                    try:
                        await self.message.edit(embed=emb)
                    except discord.errors.HTTPException:
                        pass


class CancelarView(discord.ui.View):
    """Botón "Cancelar" bajo una descarga o traducción en marcha.
    Solo lo puede pulsar quien hizo el pedido o alguien con permiso de gestionar mensajes."""

    def __init__(self, autor_id: int, al_cancelar):
        super().__init__(timeout=None)
        self.autor_id = autor_id
        self.al_cancelar = al_cancelar  # corrutina sin argumentos

    @discord.ui.button(label="Cancelar", emoji="⏹️", style=discord.ButtonStyle.danger)
    async def cancelar(self, interaction: discord.Interaction, button: discord.ui.Button):
        permisos = getattr(interaction.user, "guild_permissions", None)
        if interaction.user.id != self.autor_id and not (permisos and permisos.manage_messages):
            await interaction.response.send_message("Solo quien lo pidió puede cancelarlo.", ephemeral=True)
            return
        button.disabled = True
        button.label = "Cancelando..."
        await interaction.response.edit_message(view=self)
        await self.al_cancelar()


def _fmt_tiempo(seg: float) -> str:
    seg = int(seg)
    if seg < 60:
        return f"{seg}s"
    m, s = divmod(seg, 60)
    return f"{m}m {s:02d}s" if m < 60 else f"{m // 60}h {m % 60:02d}m"


def _barra(actual: int, total: int, ancho: int = 20) -> str:
    if not total:
        return ""
    llenos = round(ancho * min(actual, total) / total)
    return "█" * llenos + "░" * (ancho - llenos)

async def traducir_y_entregar(channel, ruta_pdf: str, solicitante: str, borrar_al_final: bool = False,
                              solicitante_id: Optional[int] = None) -> None:
    """Manda un PDF local a MangaScan, muestra el progreso y entrega el resultado."""
    nombre = os.path.basename(ruta_pdf)
    embed = discord.Embed(title=f"🌐 Traduciendo: {nombre}"[:250], color=discord.Color.purple(),
                          description="Comprobando MangaScan...")
    embed.set_footer(text=f"Pedido por {solicitante}")
    estado_cancel = {"pedido": False, "task_id": None, "hecho": False}

    async def pedir_cancelacion():
        estado_cancel["pedido"] = True
        if estado_cancel["task_id"] and not estado_cancel["hecho"]:
            estado_cancel["hecho"] = True
            await mangascan_client.cancelar(estado_cancel["task_id"])

    vista = CancelarView(solicitante_id, pedir_cancelacion) if solicitante_id else None
    mensaje = await (channel.send(embed=embed, view=vista) if vista else channel.send(embed=embed))
    ultimo = {"t": 0.0, "fase": ""}

    async def actualizar(est: mangascan_client.EstadoTraduccion, forzar: bool = False):
        if est.task_id and not estado_cancel["task_id"]:
            estado_cancel["task_id"] = est.task_id
            if estado_cancel["pedido"]:  # se pulsó Cancelar durante la subida
                estado_cancel["hecho"] = True
                await mangascan_client.cancelar(est.task_id)
        ahora = time.time()
        if not forzar and est.fase == ultimo["fase"] and ahora - ultimo["t"] < 10:
            return  # no saturar la API de Discord
        ultimo["t"], ultimo["fase"] = ahora, est.fase
        lineas = [f"**{est.fase}**"]
        if est.total:
            lineas.append(f"`{_barra(est.pagina, est.total)}` {est.pagina}/{est.total} páginas")
        if est.seg_por_pagina and est.total and est.pagina < est.total:
            restante = (est.total - est.pagina) * est.seg_por_pagina
            lineas.append(f"{est.seg_por_pagina:.1f} s/página · quedan ~{max(1, round(restante / 60))} min")
        if est.task_id:
            lineas.append(f"Tarea MangaScan: `{est.task_id}`")
        embed.description = "\n".join(lineas)
        try:
            await mensaje.edit(embed=embed)
        except discord.errors.HTTPException:
            pass

    carpeta_tmp = tempfile.mkdtemp(prefix="traduccion_")
    try:
        if not await mangascan_client.disponible():
            embed.color = discord.Color.red()
            embed.description = f"[ERROR] MangaScan no responde en `{mangascan_client.MANGASCAN_URL}`."
            await mensaje.edit(embed=embed)
            return
        async with traduccion_semaphore:
            estado, resultado = await mangascan_client.traducir_pdf(ruta_pdf, carpeta_tmp, actualizar)
        await actualizar(estado, forzar=True)
        if estado_cancel["pedido"] or estado.fase == "Cancelada":
            embed.title = f"⏹️ Traducción cancelada: {nombre}"[:250]
            embed.color = discord.Color.dark_grey()
            embed.description = "Cancelada por el usuario. MangaScan detuvo la tarea y borró sus restos."
            await mensaje.edit(embed=embed, view=None)
        elif resultado:
            embed.title = f"[SUCCESS] Traducido: {nombre}"[:250]
            embed.color = discord.Color.green()
            await mensaje.edit(embed=embed, view=None)
            await _entregar_pdf(channel, resultado)
        else:
            embed.title = f"[ERROR] No se pudo traducir: {nombre}"[:250]
            embed.color = discord.Color.red()
            if estado.errores:
                embed.add_field(name="Detalle", value=f"```\n{estado.errores[-1][:900]}\n```", inline=False)
            await mensaje.edit(embed=embed, view=None)
    except Exception as e:
        embed.color = discord.Color.red()
        embed.description = f"[ERROR] {e}"[:1000]
        try:
            await mensaje.edit(embed=embed)
        except discord.errors.HTTPException:
            pass
        traceback.print_exc()
    finally:
        # El PDF traducido queda guardado en MangaScan (Biblioteca); aquí era temporal.
        for f in os.listdir(carpeta_tmp):
            try:
                os.remove(os.path.join(carpeta_tmp, f))
            except OSError:
                pass
        try:
            os.rmdir(carpeta_tmp)
        except OSError:
            pass
        if borrar_al_final:
            try:
                os.remove(ruta_pdf)
                os.rmdir(os.path.dirname(ruta_pdf))
            except OSError:
                pass


class TraducirBoton(discord.ui.DynamicItem[discord.ui.Button], template=r"traducir:(?P<id>[0-9a-f]{16})"):
    """Botón persistente "Traducir al español": el custom_id lleva un id corto del PDF
    (índice en PDF/.traducir_index.json), así sigue funcionando tras reiniciar el bot."""

    def __init__(self, pdf_id: str):
        super().__init__(discord.ui.Button(label="Traducir al español", emoji="🌐",
                                           style=discord.ButtonStyle.primary, custom_id=f"traducir:{pdf_id}"))
        self.pdf_id = pdf_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(match["id"])

    async def callback(self, interaction: discord.Interaction):
        ruta = pdf_traducible(self.pdf_id)
        if not ruta:
            await interaction.response.send_message(
                "[ERROR] Ese PDF ya no está en el NAS. Descárgalo de nuevo con !descargar o adjúntalo a !traducir.",
                ephemeral=True)
            return
        self.item.disabled = True
        self.item.label = "Traducción en marcha"
        await interaction.response.edit_message(view=self.view)
        spawn(traducir_y_entregar(interaction.channel, ruta, interaction.user.display_name,
                                  solicitante_id=interaction.user.id), interaction.channel)


class TraducirView(discord.ui.View):
    """Vista con el botón persistente "Traducir al español" bajo cada PDF descargado.
    Solo lleva un DynamicItem, así que discord.py no la guarda en memoria tras enviarla."""

    def __init__(self, file_path: str):
        super().__init__(timeout=None)
        self.add_item(TraducirBoton(registrar_pdf_traducible(file_path)))


@bot.event
async def setup_hook():
    # Los botones «Traducir» enviados antes de un reinicio se resuelven por su custom_id
    bot.add_dynamic_items(TraducirBoton)


@bot.event
async def on_ready():
    print(f'Bot connected as {bot.user}')
    # Carpetas de imágenes que dejó una descarga interrumpida (reinicio, OOM...). Solo las de más
    # de 6 h: on_ready también salta al reconectar y no debe tocar descargas en curso.
    borradas = await asyncio.to_thread(limpiar_temporales_huerfanos, LIMPIEZA_TEMPORALES_HORAS)
    if borradas:
        print(f"[INFO] {borradas} carpetas temporales huérfanas borradas")


@bot.event
async def on_command_error(ctx, error):
    """Errores de uso con un mensaje corto en español; el resto, al log."""
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Falta un dato: `{error.param.name}`. Uso: `!{ctx.command.qualified_name} "
                       f"{ctx.command.signature}`")
        return
    if isinstance(error, commands.BadArgument):
        await ctx.send(f"Argumento no válido. Uso: `!{ctx.command.qualified_name} {ctx.command.signature}`")
        return
    original = getattr(error, "original", error)
    print(f"[ERROR] Comando {ctx.command}: {original!r}")
    traceback.print_exception(type(original), original, original.__traceback__)


# Capítulos de una serie que se procesan a la vez (cada uno con su embed). El resto espera
# su turno sin crear mensajes, para no disparar el límite de Discord (429) con series largas.
MAX_CAPITULOS_SIMULTANEOS = 2


async def _descargar_serie(ctx, capitulos, logger: "DiscordLogAdapter") -> None:
    cola: asyncio.Queue = asyncio.Queue()
    for sub_url in capitulos:
        cola.put_nowait(sub_url)

    async def trabajador():
        while not logger.cancelado:
            try:
                sub_url = cola.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                await ctx.invoke(descargar, url=sub_url)
            except Exception as e:
                print(f"[ERROR] Capítulo {sub_url}: {e!r}")
                try:
                    await ctx.send(f"[ERROR] Capítulo <{sub_url}>: {e}"[:1900])
                except discord.errors.HTTPException:
                    pass

    await asyncio.gather(*(trabajador() for _ in range(min(MAX_CAPITULOS_SIMULTANEOS, len(capitulos)))))
    if logger.cancelado:
        logger.fase = f"Serie cancelada: quedaron {cola.qsize()} capítulo(s) sin descargar"
        await logger._editar(True, quitar_boton=True, color=discord.Color.dark_grey(),
                             titulo="⏹️ Serie cancelada")
    else:
        logger.fase = f"Serie terminada: {len(capitulos)} capítulo(s) procesados"
        await logger._editar(True, quitar_boton=True, color=discord.Color.blurple(), titulo="📚 Serie")


@bot.command(name='descargar')
async def descargar(ctx, url: str):
    """
    Downloads a manga from a supported URL.
    Usage: !descargar <url>
    """
    # Un número a secas es un código de galería: el mismo número es una obra distinta en nhentai
    # y en Hitomi, así que no se descarga directamente; se enseñan las dos para elegir.
    codigo = busqueda.codigo_de(url)
    if codigo:
        await buscar_por_codigo(ctx, codigo)
        return

    # chochox/vercomicsporno bloquean la descarga automática (Cloudflare): no se les pide nada;
    # se busca la misma obra, por el título del enlace, en los sitios compatibles.
    obra = obra_desde_enlace(url)
    if obra:
        await alternativas_sitio_bloqueado(ctx, url, *obra)
        return

    # Un álbum de Bunkr es un recopilatorio: se enseña la lista de vídeos para elegir
    if _es_album_bunkr(url):
        await abrir_menu_bunkr(ctx, url)
        return

    logger = DiscordLogAdapter(ctx, url)
    if download_semaphore.locked():
        logger.fase = f"En cola (hay {MAX_DESCARGAS_SIMULTANEAS} descargas en marcha)..."
    await logger.initialize()

    try:
        async with download_semaphore:
            if logger.cancelado:  # cancelada mientras esperaba en cola
                await logger.cancelada()
                return
            logger.fase = "Abriendo la página..."
            await logger._editar(True)
            result = await core.process_entry(
                url,
                logger.log_callback,
                logger.check_cancel,
                progress_callback=logger.progress_callback,
            )

        if isinstance(result, list):
            logger.fase = (f"Serie con {len(result)} capítulos: se descargan de "
                           f"{MAX_CAPITULOS_SIMULTANEOS} en {MAX_CAPITULOS_SIMULTANEOS}")
            await logger._editar(True, color=discord.Color.blurple(), titulo="📚 Serie detectada")
            # Cada capítulo es una solicitud propia (su embed) y respeta el semáforo global;
            # «Cancelar» en este embed deja de lanzar los capítulos pendientes
            spawn(_descargar_serie(ctx, result, logger), ctx.channel)
            return

        await asyncio.sleep(0.5)
        if logger.cancelado:
            await logger.cancelada()
            return
        await logger.finalizar(bool(logger.generated_files))

        for file_path in logger.generated_files:
            filename = os.path.basename(file_path)
            try:
                # Discord (límite del servidor) o GoFile; el botón de traducir solo tiene sentido con PDF
                vista = TraducirView(file_path) if file_path.lower().endswith(".pdf") else None
                await _entregar_pdf(ctx.channel, file_path, view=vista)
            except Exception as e:
                await ctx.send(f"Error processing file `{filename}`: {e}")

        # Autor y botones «Buscar continuación» / «Más obras del autor» (solo se busca si los pulsas)
        if logger.generated_files and logger.titulo:
            spawn(ofrecer_continuacion(ctx, logger.titulo, url))

    except Exception as e:
        # Que el embed no se quede en «Descargando...» con el botón de cancelar
        try:
            logger.logs.append(f"[ERROR] {e}")
            await logger.finalizar(False)
        except Exception:
            pass
        await ctx.send(f"Critical error: {e}")
        traceback.print_exc()


# ─── Búsqueda, autor y continuaciones ─────────────────────────────────────────
# Icono y color de cada sitio en las tarjetas de resultados (una sola tabla para que no se desfasen)
SITIOS = {
    "hentainexus": ("🟣", 0x9b59b6),
    "nhentai.to": ("🔴", 0xe74c3c),
    "nhentai.xxx": ("🔴", 0xe74c3c),
    "nhentai.website": ("🔴", 0xe74c3c),
    "tmohentai.app": ("🟠", 0xe67e22),
    "lectorhentai": ("🟢", 0x2ecc71),
    "hitomi": ("🔵", 0x3498db),
    "3hentai": ("🟡", 0xf1c40f),
    "hentai2read": ("🟤", 0x8e5a3c),
    "m440": ("⚫", 0x34495e),
    "hentalk": ("🟦", 0x1abc9c),
    "erome": ("🟠", 0xe67e22),
    "bunkr": ("⚫", 0x2c3e50),
    "notfans": ("🔴", 0xe74c3c),
    "fapsly": ("🟣", 0x9b59b6),
    "thothub": ("🟪", 0x9b59b6),
    "ultrathots": ("🟧", 0xe67e22),
    "sheeshfans": ("🟨", 0xf1c40f),
    "viralxxxporn": ("🟥", 0xe74c3c),
    "xxbrits": ("🔷", 0x3498db),
    "dirtyship": ("🟫", 0x8e5a3c),
    "gotanynudes": ("🟩", 0x2ecc71),
}
_SITIO_DESCONOCIDO = ("⚪", 0x95a5a6)


def _icono_sitio(sitio: str) -> str:
    return SITIOS.get(sitio, _SITIO_DESCONOCIDO)[0]


def _color_sitio(sitio: str) -> int:
    return SITIOS.get(sitio, _SITIO_DESCONOCIDO)[1]


def _linea_resultado(i: int, r: "busqueda.Resultado") -> str:
    # Los corchetes del título ("[Círculo (Artista)] ...") rompen el enlace markdown de Discord
    t = r.titulo[:90].replace("[", "(").replace("]", ")")
    return (f"**{i}.** {busqueda.BANDERA.get(r.idioma, '🏳️')} [{t}]({r.url}) "
            f"{_icono_sitio(r.sitio)} `{r.sitio}`")


async def _bajar_portada(session: aiohttp.ClientSession, r) -> Optional[tuple]:
    """Descarga la portada de un resultado con el Referer de su sitio (varios servidores de
    imágenes rechazan otro origen, así que Discord no podría cargarlas desde el enlace)."""
    if not r.portada:
        return None
    origen = "{0.scheme}://{0.netloc}/".format(urlparse(r.url))
    try:
        async with session.get(r.portada, headers={"Referer": origen}) as resp:
            if resp.status != 200:
                return None
            datos = await resp.read()
        if not datos or len(datos) > 4 * 1024 * 1024:
            return None
        # Formato por la firma del fichero: algunos servidores (lectorhentai) envían las WebP
        # como application/octet-stream
        if datos[:3] == b"\xff\xd8\xff":
            ext = "jpg"
        elif datos[:8] == b"\x89PNG\r\n\x1a\n":
            ext = "png"
        elif datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
            ext = "webp"
        elif datos[:4] == b"GIF8":
            ext = "gif"
        else:
            return None  # no es una imagen (p. ej. una página de error)
        return datos, ext
    except Exception:
        return None


async def tarjetas_resultados(resultados, desde: int = 1):
    """Una tarjeta (embed) por resultado con su portada como miniatura adjunta. Máx. 10 por mensaje."""
    resultados = resultados[:10]
    async with aiohttp.ClientSession(headers={"User-Agent": core.config.USER_AGENT},
                                     timeout=aiohttp.ClientTimeout(total=15)) as s:
        portadas = await asyncio.gather(*(_bajar_portada(s, r) for r in resultados))
    embeds, archivos = [], []
    for i, (r, portada) in enumerate(zip(resultados, portadas), start=desde):
        descripcion = f"{busqueda.BANDERA.get(r.idioma, '🏳️')} {_icono_sitio(r.sitio)} `{r.sitio}`"
        if busqueda.es_sin_censura(r.titulo):
            descripcion += " · 🔓 sin censura"
        if r.extra.get("codigo"):  # resultado de una búsqueda por código: más datos para distinguirlos
            descripcion += f" · código **#{r.extra['codigo']}**"
            if r.extra.get("paginas"):
                descripcion += f" · {r.extra['paginas']} págs."
            if r.extra.get("artistas"):
                descripcion += f"\n✍️ {', '.join(r.extra['artistas'])[:200]}"
        e = discord.Embed(title=f"{i}. {r.titulo}"[:256], url=r.url, color=_color_sitio(r.sitio),
                          description=descripcion)
        if portada:
            nombre = f"portada_{i}.{portada[1]}"
            archivos.append(discord.File(io.BytesIO(portada[0]), filename=nombre))
            e.set_thumbnail(url=f"attachment://{nombre}")
        embeds.append(e)
    return embeds, archivos


class ElegirDescargaView(discord.ui.View):
    """Menú desplegable para descargar uno de los resultados (búsqueda o continuaciones)."""

    def __init__(self, ctx, resultados, placeholder: str = "Elige uno para descargarlo"):
        super().__init__(timeout=VISTA_TIMEOUT_SEG)
        self.ctx = ctx
        self.resultados = resultados[:25]
        menu = discord.ui.Select(
            placeholder=placeholder,
            options=[discord.SelectOption(label=f"{i}. {r.titulo}"[:100], value=str(i - 1),
                                          description=r.sitio[:100])
                     for i, r in enumerate(self.resultados, start=1)],
        )
        menu.callback = self._elegido
        self.add_item(menu)

    async def _elegido(self, interaction: discord.Interaction):
        if not _permitido(self.ctx, interaction):
            await interaction.response.send_message("Usa tu propio !buscar para descargar.", ephemeral=True)
            return
        r = self.resultados[int(interaction.data["values"][0])]
        await interaction.response.send_message(f"📥 Descargando **{r.titulo[:150]}** de `{r.sitio}`...")
        spawn(self.ctx.invoke(descargar, url=r.url), self.ctx.channel)


def _titulo_para_series(titulo: str) -> str:
    """Quita sufijos de sitio y nombres alternativos: 'Natsuzuma 3  ESPOSA DE VERANO 3  Hitomi.la' -> 'Natsuzuma 3'."""
    t = re.sub(r"\s*(?:\|\s*)?(?:Hitomi\.la|HentaiNexus|TMOHentai|nhentai)\s*$", "", titulo or "", flags=re.I)
    partes = [x.strip() for x in re.split(r"\s{2,}|\s\|\s", t) if x.strip()]
    return partes[0] if partes else t.strip()


def _fuente(r) -> str:
    """'🇪🇸 [lectorhentai](url)' para la lista de fuentes de una parte."""
    return f"{busqueda.BANDERA.get(r.idioma, '🏳️')} [{r.sitio}]({r.url})"


def _embed_serie(serie, n_serie: int, actual_url: str = "", hasta=None):
    embed = discord.Embed(title=f"📚 {serie.nombre}"[:256], color=discord.Color.teal())
    if serie.artista:
        embed.description = f"✍️ {serie.artista[:200]}"
    es_la_descargada = actual_url and any(r.url == actual_url for p in serie.partes for r in p.resultados)
    if hasta is not None and (es_la_descargada or n_serie == 0):
        n = int(hasta) if float(hasta).is_integer() else hasta
        hay_mas = any(not any(r.url == actual_url for r in p.resultados) for p in serie.partes)
        embed.set_footer(text=f"Ya tienes hasta la parte {n}: solo se muestran las siguientes." if hay_mas else
                         f"Ya tienes hasta la parte {n}. No se encontraron partes posteriores en los sitios compatibles.")
    for parte in serie.partes[:24]:
        marca = " ✅ *(la que descargaste)*" if actual_url and any(r.url == actual_url for r in parte.resultados) else ""
        fuentes = " · ".join(_fuente(r) for r in parte.resultados[:6])
        if len(parte.resultados) > 6:
            fuentes += f" · +{len(parte.resultados) - 6}"
        embed.add_field(name=f"Parte {parte.nombre}{marca}"[:256],
                        value=(fuentes or "—")[:1024], inline=False)
    return embed


class SeriesView(discord.ui.View):
    """Series agrupadas: menú para descargar una parte (mejor fuente primero: español, inglés...)
    y botón para ver la lista completa de resultados paginada."""

    def __init__(self, ctx, texto: str, series, todos, segundos: float, actual_url: str = ""):
        super().__init__(timeout=VISTA_TIMEOUT_SEG)
        self.ctx, self.texto, self.series, self.todos, self.segundos = ctx, texto, series, todos, segundos
        opciones, self.mapa = [], []
        for si, serie in enumerate(series):
            for parte in serie.partes:
                # Hasta 3 fuentes por parte (ya vienen ordenadas por idioma)
                for r in parte.resultados[:3]:
                    if len(opciones) >= 25 or (actual_url and r.url == actual_url):
                        continue
                    self.mapa.append(r)
                    opciones.append(discord.SelectOption(
                        label=f"{serie.nombre[:60]} · Parte {parte.nombre}"[:100],
                        description=f"{r.sitio} · {r.titulo}"[:100],
                        emoji=busqueda.BANDERA.get(r.idioma, "🏳️"),
                        value=str(len(self.mapa) - 1)))
        if opciones:
            menu = discord.ui.Select(placeholder="Descargar una parte", options=opciones, row=0)
            menu.callback = self._elegido
            self.add_item(menu)
        # «Versión sin censura» nada más listar (no hace falta descargar antes)
        boton = discord.ui.Button(label="🔓 Versión sin censura", style=discord.ButtonStyle.primary, row=1)
        boton.callback = self._sin_censura
        self.add_item(boton)
        if todos:
            boton = discord.ui.Button(label=f"📋 Ver todos los resultados ({len(todos)})",
                                      style=discord.ButtonStyle.secondary, row=1)
            boton.callback = self._ver_todos
            self.add_item(boton)
        # «Más obras de <artista>» (hasta 2 artistas distintos de las series mostradas)
        artistas = []
        for serie in series:
            a = (serie.artista or "").split(",")[0].strip()
            if a and a.lower() not in (x.lower() for x in artistas):
                artistas.append(a)
        for a in artistas[:2]:
            boton = discord.ui.Button(label=f"✍️ Más obras de {a}"[:80], style=discord.ButtonStyle.secondary, row=1)
            boton.callback = lambda i, a=a: self._autor(i, a)
            self.add_item(boton)

    async def _sin_censura(self, interaction: discord.Interaction):
        if not self._permitido(interaction):
            await interaction.response.send_message("Usa tu propio !buscar.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"🔓 Buscando la versión sin censura de **{busqueda.consulta_de_titulo(self.texto)[:150]}**...")
        spawn(buscar_sin_censura(self.ctx, self.texto), self.ctx.channel)

    async def _autor(self, interaction: discord.Interaction, autor: str):
        if not self._permitido(interaction):
            await interaction.response.send_message("Usa tu propio !buscar.", ephemeral=True)
            return
        await interaction.response.send_message(f"✍️ Buscando obras de **{autor[:100]}**...")
        spawn(buscar_obras_autor(self.ctx, autor), self.ctx.channel)

    def _permitido(self, interaction) -> bool:
        return _permitido(self.ctx, interaction)

    async def _elegido(self, interaction: discord.Interaction):
        if not self._permitido(interaction):
            await interaction.response.send_message("Usa tu propio !buscar para descargar.", ephemeral=True)
            return
        r = self.mapa[int(interaction.data["values"][0])]
        await interaction.response.send_message(f"📥 Descargando **{r.titulo[:150]}** de `{r.sitio}`...")
        spawn(self.ctx.invoke(descargar, url=r.url), self.ctx.channel)

    async def _ver_todos(self, interaction: discord.Interaction):
        if not self._permitido(interaction):
            await interaction.response.send_message("Usa tu propio !buscar.", ephemeral=True)
            return
        await interaction.response.defer()
        vista = ResultadosPaginadosView(self.ctx, self.texto, busqueda.ordenar_por_idioma(self.todos), self.segundos)
        contenido, tarjetas, archivos = await vista.pagina_actual()
        vista.mensaje = await interaction.followup.send(content=contenido, embeds=tarjetas, files=archivos,
                                                        view=vista, wait=True)


async def enviar_series(destino, ctx, texto: str, series, todos, segundos: float, actual_url: str = "",
                        hasta=None):
    """Un embed por serie (máx. 3) con su portada; menú para descargar y botón de lista completa."""
    embeds, archivos = [], []
    async with aiohttp.ClientSession(headers={"User-Agent": core.config.USER_AGENT},
                                     timeout=aiohttp.ClientTimeout(total=15)) as s:
        portadas = await asyncio.gather(*(_bajar_portada(s, serie.partes[0].resultados[0]) for serie in series[:3]))
    for i, (serie, portada) in enumerate(zip(series[:3], portadas)):
        e = _embed_serie(serie, i, actual_url, hasta)
        if portada:
            nombre = f"serie_{i}.{portada[1]}"
            archivos.append(discord.File(io.BytesIO(portada[0]), filename=nombre))
            e.set_thumbnail(url=f"attachment://{nombre}")
        embeds.append(e)
    n_partes = sum(len(x.partes) for x in series[:3])
    contenido = (f"📚 **«{texto[:200]}»** · {len(series)} serie(s), {n_partes} parte(s) agrupadas con IA "
                 f"de {len(todos)} resultado(s) · {segundos:.1f}s")
    vista = SeriesView(ctx, texto, series[:3], todos, segundos, actual_url)
    return await destino.send(content=contenido[:2000], embeds=embeds, files=archivos, view=vista)


async def buscar_continuacion(ctx, titulo: str, url: str = ""):
    """Otras partes de la serie de una obra, en los sitios compatibles. Solo busca por el título
    (sin las obras del artista: eso es el botón «Más obras del autor»). Primero agrupa con DeepSeek
    (series > partes > fuentes); si no puede, usa la detección por número de parte."""
    base = _titulo_para_series(titulo)
    t0 = time.time()
    try:
        series, todos, _ = await agrupador.series_para(base, actual=titulo)
        if series:
            # Si bajaste "1-3", no ofrecer otra vez la 1, la 2 ni la 3: solo lo posterior
            series, hasta = agrupador.solo_posteriores(series, url, titulo)
            await enviar_series(ctx, ctx, base, series, todos, time.time() - t0, actual_url=url, hasta=hasta)
            return
    except Exception as e:
        print(f"[WARN] Agrupar series (continuación): {e}")
    try:
        siguientes, relacionadas = await busqueda.continuaciones(base, url)
    except Exception as e:
        print(f"[WARN] Continuaciones: {e}")
        siguientes, relacionadas = [], []
    embed = discord.Embed(title=f"🔎 Continuación de {base}"[:250], color=discord.Color.teal())
    lista = siguientes + relacionadas
    if not lista:
        embed.description = "No encontré continuaciones en los sitios compatibles."
        await ctx.send(embed=embed)
        return
    if siguientes:
        embed.add_field(name="➡️ Continuaciones encontradas",
                        value="\n".join(_linea_resultado(i, r) for i, r in enumerate(siguientes, 1))[:1020], inline=False)
    if relacionadas:
        embed.add_field(name="🔗 Relacionadas",
                        value="\n".join(_linea_resultado(i, r) for i, r in enumerate(relacionadas, len(siguientes) + 1))[:1020],
                        inline=False)
    # Cabecera y una tarjeta con portada por cada parte encontrada (máx. 9 + cabecera)
    tarjetas, archivos = await tarjetas_resultados(lista[:9])
    await ctx.send(embeds=[embed] + tarjetas, files=archivos,
                   view=ElegirDescargaView(ctx, lista, "Descargar una continuación"))


async def buscar_obras_autor(ctx, autor: str, url_actual: str = ""):
    """Todas las obras de un autor en los sitios compatibles (lista paginada de tarjetas)."""
    t0 = time.time()
    resultados = [r for r in await busqueda.buscar(autor, limite=50, buscadores=busqueda.BUSCADORES_MANGA)
                  if not url_actual or r.url.rstrip("/") != url_actual.rstrip("/")]
    if not resultados:
        await ctx.send(embed=discord.Embed(title=f"✍️ {autor[:200]}",
                                           description="No encontré más obras suyas en los sitios compatibles.",
                                           color=discord.Color.orange()))
        return
    vista = ResultadosPaginadosView(ctx, f"✍️ {autor}", busqueda.ordenar_por_idioma(resultados), time.time() - t0)
    contenido, tarjetas, archivos = await vista.pagina_actual()
    vista.mensaje = await ctx.send(content=contenido, embeds=tarjetas, files=archivos, view=vista)


async def buscar_sin_censura(ctx, titulo: str, url: str = ""):
    """Versiones sin censura (uncensored / decensored / sin censura) de una obra en todos los sitios."""
    t0 = time.time()
    consulta, resultados = await busqueda.version_sin_censura(titulo, url)
    if not resultados:
        await ctx.send(embed=discord.Embed(
            title=f"🔓 {consulta[:200]}",
            description="No encontré una versión sin censura de esta obra en los sitios compatibles.",
            color=discord.Color.orange()))
        return
    vista = ResultadosPaginadosView(ctx, f"🔓 {consulta} · sin censura", busqueda.ordenar_por_idioma(resultados),
                                    time.time() - t0)
    contenido, tarjetas, archivos = await vista.pagina_actual()
    vista.mensaje = await ctx.send(content=contenido, embeds=tarjetas, files=archivos, view=vista)


async def autor_de(r) -> Optional[str]:
    """Autor de un resultado: el que trajo la búsqueda por código o el de su ficha/título."""
    if r.extra.get("artistas"):
        return r.extra["artistas"][0]
    a = await busqueda.autor(r.url, r.titulo)
    return a.split(",")[0].strip() if a else None


class AccionesObraView(discord.ui.View):
    """Tras una descarga: botones para buscar la continuación y más obras del autor."""

    def __init__(self, ctx, titulo: str, url: str, autor: Optional[str]):
        super().__init__(timeout=VISTA_TIMEOUT_SEG)
        self.ctx, self.titulo, self.url, self.autor = ctx, titulo, url, autor
        b = discord.ui.Button(label="🔎 Buscar continuación", style=discord.ButtonStyle.primary)
        b.callback = self._continuacion
        self.add_item(b)
        if autor:
            b = discord.ui.Button(label=f"✍️ Más obras de {autor}"[:80], style=discord.ButtonStyle.secondary)
            b.callback = self._autor
            self.add_item(b)

    async def _continuacion(self, interaction: discord.Interaction):
        if not _permitido(self.ctx, interaction):
            await interaction.response.send_message("Descarga tú la obra para buscar su continuación.", ephemeral=True)
            return
        await interaction.response.send_message(f"🔎 Buscando la continuación de **{_titulo_para_series(self.titulo)[:150]}**...")
        spawn(buscar_continuacion(self.ctx, self.titulo, self.url), self.ctx.channel)

    async def _autor(self, interaction: discord.Interaction):
        if not _permitido(self.ctx, interaction):
            await interaction.response.send_message("Usa tu propio !buscar.", ephemeral=True)
            return
        await interaction.response.send_message(f"✍️ Buscando obras de **{self.autor[:100]}**...")
        spawn(buscar_obras_autor(self.ctx, self.autor, self.url), self.ctx.channel)


async def ofrecer_continuacion(ctx, titulo: str, url: str):
    """Tras una descarga: autor de la obra y botones. No busca nada más hasta que se pulsan."""
    try:
        autor = await busqueda.autor(url, titulo)
    except Exception as e:
        print(f"[WARN] Autor: {e}")
        autor = None
    autor = autor.split(",")[0].strip() if autor else None
    embed = discord.Embed(title=f"📖 {_titulo_para_series(titulo)}"[:250], color=discord.Color.teal())
    if autor:
        embed.description = f"✍️ {autor[:200]}"
    await ctx.send(embed=embed, view=AccionesObraView(ctx, titulo, url, autor))


async def alternativas_sitio_bloqueado(ctx, url: str, texto: str, artista: Optional[str], completo: str):
    """Enlace de un sitio que bloquea la descarga automática: avisa y ofrece la misma obra en los
    sitios compatibles (series agrupadas o, si no se puede, la lista paginada de !buscar)."""
    host = urlparse(url).netloc.lower().removeprefix("www.")
    motivo = "Tiene un desafío anti-bots de Cloudflare en todas sus páginas y no se intenta saltar.\n"
    aviso = discord.Embed(
        title=f"🚫 {host} bloquea la descarga automática",
        description=(motivo + f"Busco la misma obra en los sitios compatibles: **{texto[:150]}**"
                     + (f" · ✍️ {artista[:60]}" if artista else "") + " ..."),
        color=discord.Color.orange())
    mensaje = await ctx.send(embed=aviso)
    t0 = time.time()
    series, todos = None, []
    try:
        series, todos, artista = await agrupador.series_para(texto, artista=artista)
        # Si no sale nada: el artista adivinado puede ser parte del título (slug entero) y algunos
        # buscadores no casan con el número de parte ("alicia goes wonderland 2" -> sin el 2).
        sin_numero = re.sub(r"\s+\d+(?:\.\d+)?$", "", texto).strip()
        for otra in (completo, sin_numero):
            if todos or not otra or otra == texto:
                continue
            series, todos, artista = await agrupador.series_para(otra, artista=artista)
    except Exception as e:
        print(f"[WARN] Alternativas para {host}: {e}")

    if not todos:
        aviso.description = (motivo + f"No encontré **{texto[:150]}** en los sitios compatibles. Prueba `!buscar` con otras "
                             f"palabras, o descárgalo desde el navegador y usa `!traducir` con el PDF adjunto.")
        await mensaje.edit(embed=aviso)
        return
    aviso.description = (motivo + f"La misma obra en los sitios compatibles (búsqueda: **{texto[:150]}**):")
    await mensaje.edit(embed=aviso)
    if series:
        await enviar_series(ctx, ctx, texto, series, todos, time.time() - t0)
        return
    vista = ResultadosPaginadosView(ctx, texto, busqueda.ordenar_por_idioma(todos), time.time() - t0)
    contenido, tarjetas, archivos = await vista.pagina_actual()
    vista.mensaje = await ctx.send(content=contenido, embeds=tarjetas, files=archivos, view=vista)


async def buscar_por_codigo(ctx, codigo: int):
    """!buscar 177013 / !descargar 177013: la galería con ese código en nhentai (vía nhentai.to)
    y en Hitomi, una tarjeta por sitio para elegir cuál descargar."""
    espera = await ctx.send(embed=discord.Embed(title=f"🔢 Buscando el código #{codigo}...",
                                                description="nhentai (vía nhentai.to) · Hitomi",
                                                color=discord.Color.blue()))
    t0 = time.time()
    resultados = await busqueda.buscar_codigo(codigo)
    if not resultados:
        await espera.edit(embed=discord.Embed(
            title=f"🔢 #{codigo}: no existe",
            description=(f"No hay ninguna galería con el código **#{codigo}** ni en nhentai (nhentai.to) ni en "
                         f"Hitomi. Si es parte de un título y no un código, añade alguna palabra más "
                         f"(p. ej. `!buscar {codigo} <palabra del título>`)."),
            color=discord.Color.orange()))
        return
    if len(resultados) > 1:
        nota = "⚠️ El mismo número es una galería **distinta** en cada sitio: elige por título y portada."
    else:
        nota = "Solo existe en " + ("Hitomi." if resultados[0].sitio == "hitomi" else "nhentai.")
    vista = ResultadosPaginadosView(ctx, f"#{codigo}", resultados, time.time() - t0, nota=nota)
    contenido, tarjetas, archivos = await vista.pagina_actual()
    try:
        await espera.delete()
    except discord.errors.HTTPException:
        pass
    vista.mensaje = await ctx.send(content=contenido, embeds=tarjetas, files=archivos, view=vista)


@bot.command(name='twitter', aliases=['x'])
async def twitter_cmd(ctx, url: str):
    """
    Descarga los vídeos de un tuit público de X/Twitter.
    Uso: !twitter <enlace del tuit>   (también !x)
    """
    url = url.strip().strip("<>")
    host = host_de_url(url)
    if not host or not any(dominio_coincide(host, d) for d in DOMINIOS_TWITTER):
        await ctx.send("Pásame el enlace de un tuit, por ejemplo `!twitter https://x.com/usuario/status/123...`")
        return
    # Mismo flujo que !descargar (embed de progreso, cola, cancelar y entrega por Discord o GoFile)
    await ctx.invoke(descargar, url=url)


@bot.command(name='buscar')
async def buscar_cmd(ctx, *, texto: Optional[str] = None):
    """
    Busca un manga en todos los sitios compatibles.
    Uso: !buscar <título>   (sin el número para ver todas las partes: !buscar The Wife-Lenders)
         !buscar <código>   (código numérico de nhentai / Hitomi: !buscar 177013)
         !buscar <título> uncensored   (solo versiones sin censura de esa obra)
    """
    codigo = busqueda.codigo_de(texto or "")
    if codigo:
        await buscar_por_codigo(ctx, codigo)
        return
    if not texto or len(texto.strip()) < 3:
        await ctx.send("Uso: `!buscar <título>` (mínimo 3 letras). Sin número para ver todas las partes, "
                       "p. ej. `!buscar The Wife-Lenders`. Con un código de nhentai/Hitomi: `!buscar 177013`.")
        return
    texto = texto.strip()
    # "!buscar Metamorphosis uncensored" / "... sin censura": solo las versiones sin censura de esa obra
    if busqueda.PIDE_SIN_CENSURA.search(texto):
        titulo = busqueda.PIDE_SIN_CENSURA.sub(" ", texto).strip()
        if len(busqueda.normalizar(titulo)) >= 3:
            await ctx.send(f"🔓 Buscando la versión sin censura de **{titulo[:150]}**...")
            await buscar_sin_censura(ctx, titulo)
            return
    espera = await ctx.send(embed=discord.Embed(title=f"🔎 Buscando «{texto[:200]}»...",
                                                description="Hitomi · hentainexus · nhentai (to, xxx, website) · 3hentai · "
                                                            "hentai2read · m440 · hentalk · tmohentai · lectorhentai · "
                                                            "imhentai · allporncomic · sexkomix2",
                                                color=discord.Color.blue()))
    t0 = time.time()
    resultados = busqueda.ordenar_por_idioma(
        await busqueda.buscar(texto, limite=50, buscadores=busqueda.BUSCADORES_MANGA))
    embed = discord.Embed(title=f"🔎 «{texto[:200]}»", color=discord.Color.blue())
    if not resultados:
        embed.description = "Sin resultados en los sitios compatibles. Prueba con menos palabras o el título en romaji."
        await espera.edit(embed=embed)
        return

    # Agrupar en series y partes con DeepSeek (solo lo encontrado por el título: las obras del
    # artista se piden aparte con «Más obras del autor»). Si falla, lista paginada.
    try:
        todos = list(resultados)
        await espera.edit(embed=discord.Embed(title="🤖 Agrupando por series y partes...",
                                              description=f"{len(todos)} resultado(s) · DeepSeek",
                                              color=discord.Color.blue()))
        series = await agrupador.agrupar(texto, todos[:agrupador.MAX_CANDIDATOS])
    except Exception as e:
        print(f"[WARN] Agrupar series: {e}")
        series = None
    if series:
        try:
            await espera.delete()
        except discord.errors.HTTPException:
            pass
        await enviar_series(ctx, ctx, texto, series, todos, time.time() - t0)
        return

    vista = ResultadosPaginadosView(ctx, texto, resultados, time.time() - t0)
    contenido, tarjetas, archivos = await vista.pagina_actual()
    try:
        await espera.delete()
    except discord.errors.HTTPException:
        pass
    vista.mensaje = await ctx.send(content=contenido, embeds=tarjetas, files=archivos, view=vista)


@bot.command(name='leak')
async def leak_cmd(ctx, *, texto: Optional[str] = None):
    """
    Busca creadoras/modelos de OnlyFans (Erome, Bunkr, NotFans, Fapsly).
    Uso: !leak <modelo>   (p. ej. !leak hidori rose)
    """
    if not texto or len(texto.strip()) < 3:
        await ctx.send("Uso: `!leak <modelo>` (mínimo 3 letras). Busca en **Erome · Bunkr · NotFans · Fapsly**. "
                       "Ejemplo: `!leak hidori rose`.")
        return
    texto = texto.strip()
    espera = await ctx.send(embed=discord.Embed(
        title=f"🔎 Buscando «{texto[:200]}»...",
        description="Erome · Bunkr · NotFans · Fapsly (creadoras de OnlyFans)",
        color=discord.Color.blue()))
    t0 = time.time()
    resultados = await busqueda.buscar_creadoras(texto, limite=60)
    if not resultados:
        await espera.edit(embed=discord.Embed(
            title=f"🔎 «{texto[:200]}»", color=discord.Color.blue(),
            description="Sin resultados en Erome, Bunkr, NotFans ni Fapsly. Prueba con el nombre tal y como "
                        "aparece en su OnlyFans (p. ej. `!leak hidori rose`) o con menos palabras."))
        return
    vista = ResultadosPaginadosView(ctx, texto, resultados, time.time() - t0,
                                    solo_descarga=True, comando="leak")
    contenido, tarjetas, archivos = await vista.pagina_actual()
    try:
        await espera.delete()
    except discord.errors.HTTPException:
        pass
    vista.mensaje = await ctx.send(content=contenido, embeds=tarjetas, files=archivos, view=vista)


def _es_album_bunkr(url: str) -> bool:
    """Álbum de Bunkr (recopilatorio de varios vídeos), no una ficha /f/<id>."""
    return bool(re.search(r"(?:bunkr\.[a-z]+|bunkrsearch\.com)", url)) and \
        bool(re.search(r"/(?:a|albums)/[A-Za-z0-9]+", url))


async def abrir_menu_bunkr(ctx, url: str, titulo_respaldo: str = "") -> None:
    """Enseña los vídeos del álbum para elegir cuáles descargar (no baja las fotos)."""
    titulo, videos = await bunkr_site.listar_videos(url)
    titulo = titulo or titulo_respaldo or "Bunkr"
    if not videos:
        await ctx.send(f"📂 **{titulo[:150]}**: el álbum no tiene vídeos (las fotos no se descargan).")
        return
    vista = BunkrAlbumView(ctx, titulo, videos)
    vista.mensaje = await ctx.send(content=vista.texto(), view=vista)


class BunkrAlbumView(discord.ui.View):
    """Menú de un álbum de Bunkr: lista paginada de vídeos con selección múltiple."""

    POR_PAGINA = 25

    def __init__(self, ctx, titulo: str, videos):
        super().__init__(timeout=1800)
        self.ctx, self.titulo, self.videos = ctx, titulo, videos
        self.pagina = 0
        self.paginas = max(1, -(-len(videos) // self.POR_PAGINA))
        self.elegidos = set()
        self.descargando = False
        self.mensaje = None
        self._montar()

    def texto(self) -> str:
        return (f"📂 **{self.titulo[:150]}** · {len(self.videos)} vídeo(s) · elige los que quieras descargar\n"
                f"Página **{self.pagina + 1}/{self.paginas}** · seleccionados: **{len(self.elegidos)}**")

    def _de_la_pagina(self):
        inicio = self.pagina * self.POR_PAGINA
        return list(enumerate(self.videos[inicio:inicio + self.POR_PAGINA], start=inicio))

    def _montar(self):
        self.clear_items()
        opciones = [discord.SelectOption(
            label=f"{i + 1}. {nombre}"[:100],
            description=(f"{tam} · ficha {ficha.rsplit('/', 1)[-1]}" if tam else f"ficha {ficha.rsplit('/', 1)[-1]}")[:100],
            value=str(i), default=i in self.elegidos)
            for i, (nombre, ficha, tam) in self._de_la_pagina()]
        menu = discord.ui.Select(
            placeholder=f"Vídeos de la página {self.pagina + 1}/{self.paginas} (elige uno o varios)",
            min_values=0, max_values=max(1, len(opciones)), options=opciones, row=0)
        menu.callback = self._seleccionar
        self.add_item(menu)
        if self.paginas > 1:
            anterior = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary, row=1,
                                         disabled=self.pagina == 0)
            siguiente = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary, row=1,
                                          disabled=self.pagina >= self.paginas - 1)
            anterior.callback = lambda i: self._ir(i, -1)
            siguiente.callback = lambda i: self._ir(i, +1)
            self.add_item(anterior)
            self.add_item(siguiente)
        bajar = discord.ui.Button(label=f"📥 Descargar seleccionados ({len(self.elegidos)})",
                                  style=discord.ButtonStyle.success, row=2,
                                  disabled=not self.elegidos or self.descargando)
        bajar.callback = self._descargar
        self.add_item(bajar)

    def _permitido(self, interaction: discord.Interaction) -> bool:
        permisos = getattr(interaction.user, "guild_permissions", None)
        return interaction.user.id == self.ctx.author.id or bool(permisos and permisos.manage_messages)

    async def _seleccionar(self, interaction: discord.Interaction):
        if not self._permitido(interaction):
            await interaction.response.send_message("Usa tu propio `!leak` para elegir vídeos.", ephemeral=True)
            return
        inicio = self.pagina * self.POR_PAGINA
        marcados = {int(v) for v in interaction.data.get("values", [])}
        for i in range(inicio, min(inicio + self.POR_PAGINA, len(self.videos))):
            (self.elegidos.add if i in marcados else self.elegidos.discard)(i)
        self._montar()
        await interaction.response.edit_message(content=self.texto(), view=self)

    async def _ir(self, interaction: discord.Interaction, paso: int):
        if not self._permitido(interaction):
            await interaction.response.send_message("Usa tu propio `!leak` para pasar de página.", ephemeral=True)
            return
        self.pagina = min(max(0, self.pagina + paso), self.paginas - 1)
        self._montar()
        await interaction.response.edit_message(content=self.texto(), view=self)

    async def _descargar(self, interaction: discord.Interaction):
        if not self._permitido(interaction):
            await interaction.response.send_message("Usa tu propio `!leak` para descargar.", ephemeral=True)
            return
        if self.descargando:
            await interaction.response.send_message("Ya se están descargando.", ephemeral=True)
            return
        items = [self.videos[i] for i in sorted(self.elegidos)]
        if not items:
            await interaction.response.send_message("No has elegido ningún vídeo.", ephemeral=True)
            return
        self.descargando = True
        self._montar()
        await interaction.response.edit_message(content=self.texto(), view=self)
        await interaction.channel.send(f"📥 Descargando {len(items)} vídeo(s) de **{self.titulo[:150]}**...")
        bot.loop.create_task(_bajar_seleccion_bunkr(self.ctx, self.titulo, items))


async def _bajar_seleccion_bunkr(ctx, titulo: str, items) -> None:
    """Descarga los vídeos elegidos en el menú de un álbum, con el embed de progreso."""
    logger = DiscordLogAdapter(ctx, items[0][1])
    logger.titulo = titulo
    await logger.initialize()
    try:
        async with download_semaphore:
            await bunkr_site.descargar_videos(titulo, items, logger.log_callback, logger.check_cancel)
        await logger.finalizar(bool(logger.generated_files))
        for ruta in logger.generated_files:
            try:
                await _entregar_pdf(ctx.channel, ruta)
            except Exception as e:
                await ctx.send(f"Error al entregar `{os.path.basename(ruta)}`: {e}")
    except Exception as e:
        await ctx.send(f"Error al descargar el álbum: {e}")


class ResultadosPaginadosView(discord.ui.View):
    """Resultados de !buscar en páginas de 10 tarjetas (el máximo de Discord por mensaje), con
    ◀ ▶ para pasar de página y un menú para descargar cualquiera de la página actual."""

    POR_PAGINA = 10

    def __init__(self, ctx, texto: str, resultados, segundos: float, nota: str = "",
                 solo_descarga: bool = False, comando: str = "buscar"):
        super().__init__(timeout=VISTA_TIMEOUT_SEG)
        self.ctx, self.texto, self.resultados, self.segundos = ctx, texto, resultados, segundos
        self.nota = nota  # línea extra bajo la cabecera (p. ej. el aviso de la búsqueda por código)
        # !leak: solo descargar (no tiene sentido «continuación», «autor» ni «sin censura»)
        self.solo_descarga = solo_descarga
        self.comando = comando
        self.pagina = 0
        self.paginas = max(1, -(-len(resultados) // self.POR_PAGINA))
        self.mensaje = None
        self._montar()

    def _de_la_pagina(self):
        i = self.pagina * self.POR_PAGINA
        return self.resultados[i:i + self.POR_PAGINA]

    def _montar(self):
        self.clear_items()
        inicio = self.pagina * self.POR_PAGINA
        menu = discord.ui.Select(
            placeholder=f"Elige uno para descargarlo (página {self.pagina + 1}/{self.paginas})",
            options=[discord.SelectOption(label=f"{i}. {r.titulo}"[:100], value=str(i - 1),
                                          description=r.sitio[:100], emoji=busqueda.BANDERA.get(r.idioma, "🏳️"))
                     for i, r in enumerate(self._de_la_pagina(), start=inicio + 1)],
            row=0,
        )
        menu.callback = self._elegido
        self.add_item(menu)
        if not self.solo_descarga:
            for fila, (texto, callback) in enumerate((("🔎 Buscar continuación de…", self._continuacion),
                                                     ("✍️ Más obras del autor de…", self._autor),
                                                     ("🔓 Buscar versión sin censura de…", self._sin_censura)), start=2):
                extra = discord.ui.Select(
                    placeholder=texto,
                    options=[discord.SelectOption(label=f"{i}. {r.titulo}"[:100], value=str(i - 1),
                                                  description=r.sitio[:100])
                             for i, r in enumerate(self._de_la_pagina(), start=inicio + 1)],
                    row=fila,
                )
                extra.callback = callback
                self.add_item(extra)
        if self.paginas > 1:
            anterior = discord.ui.Button(label="◀ Anterior", style=discord.ButtonStyle.secondary,
                                         disabled=self.pagina == 0, row=1)
            siguiente = discord.ui.Button(label="Siguiente ▶", style=discord.ButtonStyle.secondary,
                                          disabled=self.pagina >= self.paginas - 1, row=1)
            anterior.callback = lambda i: self._ir(i, -1)
            siguiente.callback = lambda i: self._ir(i, +1)
            self.add_item(anterior)
            self.add_item(siguiente)

    async def pagina_actual(self):
        if self.solo_descarga:
            sitios = " · ".join(sorted({r.sitio for r in self.resultados}))
            contenido = (f"🔎 **«{self.texto[:200]}»** · {len(self.resultados)} resultado(s) · "
                         f"{self.segundos:.1f}s · página **{self.pagina + 1}/{self.paginas}**\n"
                         f"OnlyFans leaks · {sitios}")
            if self.nota:
                contenido += f"\n{self.nota}"
            tarjetas, archivos = await tarjetas_resultados(self._de_la_pagina(),
                                                           desde=self.pagina * self.POR_PAGINA + 1)
            return contenido[:2000], tarjetas, archivos
        cuenta = {}
        for r in self.resultados:
            cuenta[r.idioma] = cuenta.get(r.idioma, 0) + 1
        idiomas = " · ".join(f"{busqueda.BANDERA.get(k, '🏳️')} {v}"
                             for k, v in sorted(cuenta.items(), key=lambda kv: busqueda._PRIORIDAD.get(kv[0], 4)))
        sitios = len({r.sitio for r in self.resultados})
        contenido = (f"🔎 **«{self.texto[:200]}»** · {len(self.resultados)} resultado(s) en {sitios} sitio(s) · "
                     f"{self.segundos:.1f}s · página **{self.pagina + 1}/{self.paginas}**\n{idiomas}")
        if self.nota:
            contenido += f"\n{self.nota}"
        tarjetas, archivos = await tarjetas_resultados(self._de_la_pagina(), desde=self.pagina * self.POR_PAGINA + 1)
        return contenido[:2000], tarjetas, archivos

    def _permitido(self, interaction: discord.Interaction) -> bool:
        return _permitido(self.ctx, interaction)

    async def _ir(self, interaction: discord.Interaction, paso: int):
        if not self._permitido(interaction):
            await interaction.response.send_message(f"Usa tu propio !{self.comando} para pasar de página.",
                                                    ephemeral=True)
            return
        self.pagina = min(max(0, self.pagina + paso), self.paginas - 1)
        await interaction.response.defer()  # las portadas tardan un poco en descargarse
        self._montar()
        contenido, tarjetas, archivos = await self.pagina_actual()
        await interaction.message.edit(content=contenido, embeds=tarjetas, attachments=archivos, view=self)

    async def _elegido(self, interaction: discord.Interaction):
        if not self._permitido(interaction):
            await interaction.response.send_message(f"Usa tu propio !{self.comando} para descargar.", ephemeral=True)
            return
        r = self.resultados[int(interaction.data["values"][0])]
        if _es_album_bunkr(r.url):
            await interaction.response.send_message(
                f"📂 Leyendo el álbum **{r.titulo[:150]}** para enseñarte sus vídeos...")
            bot.loop.create_task(abrir_menu_bunkr(self.ctx, r.url, r.titulo))
            return
        await interaction.response.send_message(f"📥 Descargando **{r.titulo[:150]}** de `{r.sitio}`...")
        spawn(self.ctx.invoke(descargar, url=r.url), self.ctx.channel)

    async def _continuacion(self, interaction: discord.Interaction):
        if not self._permitido(interaction):
            await interaction.response.send_message("Usa tu propio !buscar.", ephemeral=True)
            return
        r = self.resultados[int(interaction.data["values"][0])]
        await interaction.response.send_message(f"🔎 Buscando la continuación de **{_titulo_para_series(r.titulo)[:150]}**...")
        spawn(buscar_continuacion(self.ctx, r.titulo, r.url), self.ctx.channel)

    async def _autor(self, interaction: discord.Interaction):
        if not self._permitido(interaction):
            await interaction.response.send_message("Usa tu propio !buscar.", ephemeral=True)
            return
        r = self.resultados[int(interaction.data["values"][0])]
        await interaction.response.send_message(f"✍️ Buscando el autor de **{r.titulo[:150]}**...")

        async def seguir():
            autor = await autor_de(r)
            if not autor:
                await self.ctx.send(f"No encontré el autor de **{r.titulo[:150]}**.")
                return
            await buscar_obras_autor(self.ctx, autor, r.url)
        spawn(seguir(), self.ctx.channel)

    async def _sin_censura(self, interaction: discord.Interaction):
        if not self._permitido(interaction):
            await interaction.response.send_message("Usa tu propio !buscar.", ephemeral=True)
            return
        r = self.resultados[int(interaction.data["values"][0])]
        await interaction.response.send_message(
            f"🔓 Buscando la versión sin censura de **{busqueda.consulta_de_titulo(r.titulo)[:150]}**...")
        spawn(buscar_sin_censura(self.ctx, r.titulo, r.url), self.ctx.channel)


@bot.command(name='traducir')
async def traducir(ctx, *, enlace: Optional[str] = None):  # '*': el nombre puede tener espacios
    """
    Traduce un manga al español con MangaScan AI.
    Usos:
      !traducir <enlace de GoFile>   (un PDF que este bot descargó y subió a GoFile)
      !traducir <nombre.pdf>         (un PDF que ya está en la carpeta del bot)
      !traducir  + PDF adjunto       (PDF pequeño de otro sitio)
    """
    adjuntos = [a for a in ctx.message.attachments if a.filename.lower().endswith(".pdf")]
    if adjuntos:
        for a in adjuntos:
            carpeta = tempfile.mkdtemp(prefix="adjunto_")
            ruta = os.path.join(carpeta, os.path.basename(a.filename) or "adjunto.pdf")
            entregado = False
            try:
                await a.save(ruta)
                # A partir de aquí traducir_y_entregar borra el PDF y la carpeta (borrar_al_final)
                spawn(traducir_y_entregar(ctx.channel, ruta, ctx.author.display_name, borrar_al_final=True,
                                          solicitante_id=ctx.author.id), ctx.channel)
                entregado = True
            except Exception as e:
                await ctx.send(f"[ERROR] No se pudo leer el adjunto `{a.filename}`: {e}"[:1900])
            finally:
                if not entregado:
                    shutil.rmtree(carpeta, ignore_errors=True)
        return

    if enlace:
        enlace = enlace.strip().strip('"').strip("<>")  # comillas o <enlace> de Discord
        if "gofile.io" in enlace:
            ruta = _pdf_de_gofile(enlace)
        else:  # recorre PDF/: fuera del event loop
            ruta = await asyncio.to_thread(_find_pdf_fast, os.path.basename(enlace))
        if ruta:
            spawn(traducir_y_entregar(ctx.channel, ruta, ctx.author.display_name,
                                      solicitante_id=ctx.author.id), ctx.channel)
        elif "gofile.io" in enlace:
            await ctx.send("[ERROR] No encuentro en el NAS el PDF de ese enlace. Solo funciona con enlaces "
                           "que subió este bot; si tienes el PDF, adjúntalo o descárgalo de nuevo con !descargar.")
        else:
            await ctx.send(f"[ERROR] No encuentro `{enlace}` en la carpeta del bot.")
        return

    await ctx.send("Uso: `!traducir <enlace de GoFile>`, `!traducir <nombre.pdf>` o `!traducir` con un PDF adjunto. "
                   "También puedes pulsar **🌐 Traducir al español** bajo cualquier PDF descargado.")


if __name__ == "__main__":
    if not TOKEN:
        print("ERROR: DISCORD_TOKEN not found in .env")
    else:
        bot.run(TOKEN)
