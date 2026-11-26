
"""
Module: bot
Description: A Discord API integration (Discord Bot) for the Manga Downloader.
It allows users to trigger downloads via Discord commands (e.g., !descargar <url>).
The bot features live-updating embed messages for progress logs and automatically 
uploads files exceeding Discord's 8MB limit to GoFile for easy user retrieval.

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
import aiohttp
import io
import json
import tempfile
import time
from typing import Optional, Dict
from dotenv import load_dotenv

import core
import core.config
import mangascan_client
from core import busqueda

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# Configure core for Bot Mode (Silent)
core.config.OPEN_RESULT_ON_FINISH = False

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

# Semaphore to allow up to 3 concurrent downloads (instead of a global Lock)
download_semaphore = asyncio.Semaphore(3)

# PDF file index cache: maps filename -> full path for O(1) lookup
# Built lazily and refreshed on cache miss
_pdf_index_cache: Dict[str, str] = {}
_pdf_index_built = False


def _build_pdf_index():
    """Builds a filename->fullpath index of the PDF folder for O(1) lookups."""
    global _pdf_index_cache, _pdf_index_built
    current_dir = os.getcwd()
    pdf_root = os.path.join(current_dir, core.config.PDF_FOLDER_NAME)
    _pdf_index_cache.clear()
    if os.path.exists(pdf_root):
        for root, dirs, files in os.walk(pdf_root):
            for fname in files:
                _pdf_index_cache[fname] = os.path.join(root, fname)
    _pdf_index_built = True


def _find_pdf_fast(filename: str) -> Optional[str]:
    """O(1) lookup for a PDF file in the PDF directory. Rebuilds index on cache miss."""
    global _pdf_index_cache, _pdf_index_built
    if not _pdf_index_built:
        _build_pdf_index()
    
    result = _pdf_index_cache.get(filename)
    if result and os.path.exists(result):
        return result
    
    # Cache miss or stale entry: rebuild and retry
    _build_pdf_index()
    return _pdf_index_cache.get(filename)

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
        if self.total:
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
            bot.loop.create_task(self._editar(forzar))
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
            self.fase = "Descargando páginas..."
            forzar = True
        elif "Generating PDF" in text:
            self.fase = "Generando el PDF..."
            if self.total:
                self.actual = self.total
            forzar = True
        elif "[INIT]" in text or "Opening reader" in text:
            self.fase = "Abriendo la página..."
        elif "Retrying" in text:
            self.reintentos += 1
        elif m := re.search(r"(\d+) de \d+ páginas no se pudieron descargar", text):
            self.paginas_perdidas = int(m.group(1))

        # PDF generado: "[SUCCESS] PDF Generated: file.pdf"
        if m := re.search(r"\[SUCCESS\] PDF Generated: (.*)", text):
            filename = m.group(1).strip()
            if os.path.isabs(filename) and os.path.exists(filename):
                self.generated_files.append(filename)
            else:
                result = _find_pdf_fast(filename)
                if result:
                    self.generated_files.append(result)
                elif os.path.exists(filename):
                    self.generated_files.append(filename)

        self._programar(forzar)

    async def finalizar(self, ok: bool):
        duracion = _fmt_tiempo(time.time() - self.inicio)
        if ok:
            tam = sum(os.path.getsize(f) for f in self.generated_files if os.path.exists(f)) / (1024 * 1024)
            completas = (self.total or 0) - self.paginas_perdidas
            self.fase = f"Listo en {duracion} · {completas}/{self.total or '?'} páginas · {tam:.1f} MB"
            self.actual = self.total
            self.reintentos = 0  # ya no importan: todo llegó (o se avisa de lo que falta)
            color = discord.Color.green() if not self.paginas_perdidas else discord.Color.orange()
            icono = "✅" if not self.paginas_perdidas else "⚠️"
            await self._editar(True, quitar_boton=True, color=color, titulo=f"{icono} Descargado: {self.titulo or 'manga'}")
        else:
            detalle = "\n".join(l for l in self.logs if "[ERROR]" in l or "[WARN]" in l)[-900:]
            self.fase = f"No se generó ningún PDF ({duracion})"
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

_gofile_token: Optional[str] = None
_gofile_root: Optional[str] = None
_gofile_public_folder: Optional[str] = None


async def _ensure_gofile_account() -> bool:
    """Creates/reuses a GoFile guest account token (the API now requires auth)."""
    global _gofile_token, _gofile_root
    if _gofile_token:
        return True
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post('https://api.gofile.io/accounts', json={}) as resp:
                data = await resp.json()
                if resp.status == 200 and data.get('status') == 'ok':
                    _gofile_token = data['data'].get('token')
                    _gofile_root = data['data'].get('rootFolder')
                    return bool(_gofile_token)
    except Exception as e:
        print(f"[ERROR GOFILE] account: {e}")
    return False


async def _ensure_gofile_public_folder() -> Optional[str]:
    """Ensures a public folder exists in the GoFile account and returns its id.

    Files must live inside a *public* folder for their download page to be
    shareable; by default content is private ("not publicly accessible").
    """
    global _gofile_public_folder
    if _gofile_public_folder:
        return _gofile_public_folder
    if not await _ensure_gofile_account():
        return None
    try:
        headers = {'Authorization': f'Bearer {_gofile_token}'}
        async with aiohttp.ClientSession() as session:
            payload = {
                'parentFolderId': _gofile_root,
                'folderName': 'manga',
                'public': True,
            }
            async with session.post(
                'https://api.gofile.io/contents/createFolder',
                json=payload,
                headers=headers,
            ) as resp:
                data = await resp.json()
                if resp.status == 200 and data.get('status') == 'ok':
                    _gofile_public_folder = data['data'].get('id')
    except Exception as e:
        print(f"[ERROR GOFILE] public folder: {e}")
    return _gofile_public_folder


async def upload_to_gofile(file_path: str) -> Optional[str]:
    """Uploads a file to GoFile and returns the download link."""
    try:
        if not await _ensure_gofile_account():
            return None
        headers = {'Authorization': f'Bearer {_gofile_token}'}
        folder_id = await _ensure_gofile_public_folder() or _gofile_root
        async with aiohttp.ClientSession() as session:
            async with session.get('https://api.gofile.io/servers', headers=headers) as resp:
                if resp.status != 200: return None
                data = await resp.json()
                if data.get('status') != 'ok': return None
                
                server = data['data']['servers'][0]['name']
                upload_url = f'https://{server}.gofile.io/uploadFile'
            
            filename = os.path.basename(file_path)
            ascii_filename = filename.encode('ascii', 'ignore').decode('ascii').replace(" ", "_")
            # Strip leading punctuation/underscores left by removed non-ASCII chars
            ascii_filename = ascii_filename.lstrip('._- ')
            if not ascii_filename.replace("_", "").replace(".pdf", ""):
                ascii_filename = "manga_download.pdf"

            with open(file_path, 'rb') as f:
                form_data = aiohttp.FormData(quote_fields=False)
                form_data.add_field('file', f, filename=ascii_filename, content_type='application/pdf')
                if folder_id:
                    form_data.add_field('folderId', folder_id)
                
                async with session.post(upload_url, data=form_data, headers=headers) as upload_resp:
                    if upload_resp.status == 200:
                        res = await upload_resp.json()
                        if res.get('status') == 'ok':
                            return res['data']['downloadPage']
    except Exception as e:
        print(f"[ERROR GOFILE] {e}")
    return None


# ─── Traducción con MangaScan AI ──────────────────────────────────────────────
# Índice "enlace de GoFile -> PDF local", para que !traducir <enlace> use la copia
# del NAS en vez de volver a descargar de GoFile lo que el propio bot subió.
_GOFILE_INDEX = os.path.join(os.getcwd(), core.config.PDF_FOLDER_NAME, ".gofile_index.json")
traduccion_semaphore = asyncio.Semaphore(2)  # MangaScan ya encola; esto evita subir 10 a la vez


def _registrar_gofile(link: str, file_path: str) -> None:
    try:
        datos = {}
        if os.path.exists(_GOFILE_INDEX):
            with open(_GOFILE_INDEX, encoding="utf-8") as f:
                datos = json.load(f)
        datos[link.rstrip("/")] = os.path.abspath(file_path)
        with open(_GOFILE_INDEX, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print(f"[WARN] No se pudo registrar el enlace de GoFile: {e}")


def _pdf_de_gofile(link: str) -> Optional[str]:
    try:
        with open(_GOFILE_INDEX, encoding="utf-8") as f:
            ruta = json.load(f).get(link.rstrip("/"))
        return ruta if ruta and os.path.exists(ruta) else None
    except Exception:
        return None


async def _entregar_pdf(channel, file_path: str, view: Optional[discord.ui.View] = None) -> Optional[str]:
    """Envía un PDF por Discord, o por GoFile si supera el límite. Devuelve el enlace de GoFile si lo hubo."""
    filename = os.path.basename(file_path)
    size_mb = os.path.getsize(file_path) / (1024 * 1024)
    if size_mb > 7.9:
        await channel.send(f"[WARN] `{filename}` ({size_mb:.2f}MB) exceeds limit. Uploading to GoFile...")
        link = await upload_to_gofile(file_path)
        if link:
            # Solo los PDF que se quedan en la carpeta del bot (las traducciones son temporales)
            if os.path.abspath(file_path).startswith(os.path.dirname(_GOFILE_INDEX)):
                _registrar_gofile(link, file_path)
            if view is not None:
                await channel.send(f"[SUCCESS] **{filename}**: {link}", view=view)
            else:
                await channel.send(f"[SUCCESS] **{filename}**: {link}")
        else:
            await channel.send(f"[ERROR] Failed to upload `{filename}` to GoFile.")
        return link
    if view is not None:
        await channel.send(file=discord.File(file_path), view=view)
    else:
        await channel.send(file=discord.File(file_path))
    return None




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
        import traceback
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


class TraducirView(discord.ui.View):
    """Botón "Traducir al español" bajo cada PDF descargado."""

    def __init__(self, file_path: str):
        super().__init__(timeout=None)  # vive mientras el bot no se reinicie; después, !traducir <enlace>
        self.file_path = file_path

    @discord.ui.button(label="Traducir al español", emoji="🌐", style=discord.ButtonStyle.primary)
    async def traducir(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not os.path.exists(self.file_path):
            await interaction.response.send_message("[ERROR] Ese PDF ya no está en el NAS.", ephemeral=True)
            return
        button.disabled = True
        button.label = "Traducción en marcha"
        await interaction.response.edit_message(view=self)
        bot.loop.create_task(traducir_y_entregar(interaction.channel, self.file_path, interaction.user.display_name,
                                                     solicitante_id=interaction.user.id))

@bot.event
async def on_ready():
    print(f'Bot connected as {bot.user}')

@bot.command(name='descargar')
async def descargar(ctx, url: str):
    """
    Downloads a manga from a supported URL.
    Usage: !descargar <url>
    """
    if not url:
        await ctx.send("Please provide a URL.")
        return

    logger = DiscordLogAdapter(ctx, url)
    if download_semaphore.locked():
        logger.fase = "En cola (hay 3 descargas en marcha)..."
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
            logger.fase = f"Serie con {len(result)} capítulos: se descargan uno a uno"
            await logger._editar(True, color=discord.Color.blurple(), titulo="📚 Serie detectada")
            for sub_url in result:
                # Cada capítulo es una solicitud propia (su embed) y respeta el semáforo global
                bot.loop.create_task(ctx.invoke(descargar, url=sub_url))
            return

        await asyncio.sleep(0.5)
        if logger.cancelado:
            await logger.cancelada()
            return
        await logger.finalizar(bool(logger.generated_files))

        for file_path in logger.generated_files:
            filename = os.path.basename(file_path)
            try:
                # Discord (8MB safe limit) o GoFile, con botón para traducirlo con MangaScan
                await _entregar_pdf(ctx.channel, file_path, view=TraducirView(file_path))
            except Exception as e:
                await ctx.send(f"Error processing file `{filename}`: {e}")

        # Autor y continuaciones en los sitios compatibles (no retrasa la entrega)
        if logger.generated_files and logger.titulo:
            bot.loop.create_task(informar_autor_y_continuaciones(ctx, logger.titulo, url))

    except Exception as e:
        await ctx.send(f"Critical error: {e}")
        import traceback
        traceback.print_exc()


# ─── Búsqueda, autor y continuaciones ─────────────────────────────────────────
ICONO_SITIO = {"hentainexus": "🟣", "nhentai.to": "🔴", "nhentai.xxx": "🔴", "nhentai.website": "🔴",
               "tmohentai.app": "🟠", "lectorhentai": "🟢", "hitomi": "🔵", "3hentai": "🟡", "hentai2read": "🟤", "m440": "⚫"}


def _linea_resultado(i: int, r: "busqueda.Resultado") -> str:
    return f"**{i}.** [{r.titulo[:90]}]({r.url}) {ICONO_SITIO.get(r.sitio, '⚪')} `{r.sitio}`"


COLOR_SITIO = {"hentainexus": 0x9b59b6, "nhentai.to": 0xe74c3c, "nhentai.xxx": 0xe74c3c, "nhentai.website": 0xe74c3c,
               "tmohentai.app": 0xe67e22, "lectorhentai": 0x2ecc71, "hitomi": 0x3498db,
               "3hentai": 0xf1c40f, "hentai2read": 0x8e5a3c, "m440": 0x34495e}


async def _bajar_portada(session: aiohttp.ClientSession, r) -> Optional[tuple]:
    """Descarga la portada de un resultado con el Referer de su sitio (varios servidores de
    imágenes rechazan otro origen, así que Discord no podría cargarlas desde el enlace)."""
    if not r.portada:
        return None
    from urllib.parse import urlparse
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
        e = discord.Embed(title=f"{i}. {r.titulo}"[:256], url=r.url, color=COLOR_SITIO.get(r.sitio, 0x95a5a6),
                          description=f"{ICONO_SITIO.get(r.sitio, '⚪')} `{r.sitio}`")
        if portada:
            nombre = f"portada_{i}.{portada[1]}"
            archivos.append(discord.File(io.BytesIO(portada[0]), filename=nombre))
            e.set_thumbnail(url=f"attachment://{nombre}")
        embeds.append(e)
    return embeds, archivos


class ElegirDescargaView(discord.ui.View):
    """Menú desplegable para descargar uno de los resultados (búsqueda o continuaciones)."""

    def __init__(self, ctx, resultados, placeholder: str = "Elige uno para descargarlo"):
        super().__init__(timeout=1800)
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
        permisos = getattr(interaction.user, "guild_permissions", None)
        if interaction.user.id != self.ctx.author.id and not (permisos and permisos.manage_messages):
            await interaction.response.send_message("Usa tu propio !buscar para descargar.", ephemeral=True)
            return
        r = self.resultados[int(interaction.data["values"][0])]
        await interaction.response.send_message(f"📥 Descargando **{r.titulo[:150]}** de `{r.sitio}`...")
        bot.loop.create_task(self.ctx.invoke(descargar, url=r.url))


def _titulo_para_series(titulo: str) -> str:
    """Quita sufijos de sitio y nombres alternativos: 'Natsuzuma 3  ESPOSA DE VERANO 3  Hitomi.la' -> 'Natsuzuma 3'."""
    t = re.sub(r"\s*(?:\|\s*)?(?:Hitomi\.la|HentaiNexus|TMOHentai|nhentai)\s*$", "", titulo or "", flags=re.I)
    partes = [x.strip() for x in re.split(r"\s{2,}|\s\|\s", t) if x.strip()]
    return partes[0] if partes else t.strip()


async def informar_autor_y_continuaciones(ctx, titulo: str, url: str):
    """Tras una descarga: autor y otras partes de la serie en los sitios compatibles."""
    try:
        base = _titulo_para_series(titulo)
        autor, (siguientes, relacionadas) = await asyncio.gather(
            busqueda.autor(url, titulo), busqueda.continuaciones(base, url))
    except Exception as e:
        print(f"[WARN] Autor/continuaciones: {e}")
        return
    if not (autor or siguientes or relacionadas):
        return
    embed = discord.Embed(title=f"📖 {base}"[:250], color=discord.Color.teal())
    if autor:
        embed.add_field(name="✍️ Autor", value=autor[:1000], inline=False)
    lista = siguientes + relacionadas
    if siguientes:
        embed.add_field(name="➡️ Continuaciones encontradas",
                        value="\n".join(_linea_resultado(i, r) for i, r in enumerate(siguientes, 1))[:1020], inline=False)
    if relacionadas:
        embed.add_field(name="🔗 Relacionadas",
                        value="\n".join(_linea_resultado(i, r) for i, r in enumerate(relacionadas, len(siguientes) + 1))[:1020],
                        inline=False)
    if not lista:
        embed.description = "No encontré continuaciones en los sitios compatibles."
        await ctx.send(embed=embed)
        return
    # Cabecera (autor + listas) y una tarjeta con portada por cada parte encontrada (máx. 9 + cabecera)
    tarjetas, archivos = await tarjetas_resultados(lista[:9])
    await ctx.send(embeds=[embed] + tarjetas, files=archivos,
                   view=ElegirDescargaView(ctx, lista, "Descargar una continuación"))


@bot.command(name='buscar')
async def buscar_cmd(ctx, *, texto: Optional[str] = None):
    """
    Busca un manga en todos los sitios compatibles.
    Uso: !buscar <título>   (sin el número para ver todas las partes: !buscar The Wife-Lenders)
    """
    if not texto or len(texto.strip()) < 3:
        await ctx.send("Uso: `!buscar <título>` (mínimo 3 letras). Sin número para ver todas las partes, "
                       "p. ej. `!buscar The Wife-Lenders`.")
        return
    texto = texto.strip()
    espera = await ctx.send(embed=discord.Embed(title=f"🔎 Buscando «{texto[:200]}»...",
                                                description="Hitomi · hentainexus · nhentai (to, xxx, website) · 3hentai · "
                                                            "hentai2read · m440 · tmohentai · lectorhentai",
                                                color=discord.Color.blue()))
    t0 = time.time()
    resultados = await busqueda.buscar(texto)
    embed = discord.Embed(title=f"🔎 «{texto[:200]}»", color=discord.Color.blue())
    if not resultados:
        embed.description = "Sin resultados en los sitios compatibles. Prueba con menos palabras o el título en romaji."
        await espera.edit(embed=embed)
        return
    mostrados = resultados[:15]
    sitios = sorted({r.sitio for r in resultados})
    tarjetas, archivos = await tarjetas_resultados(mostrados[:10])
    cabecera = (f"🔎 **«{texto[:200]}»** · {len(resultados)} resultado(s) en {len(sitios)} sitio(s) · "
                f"{time.time() - t0:.1f}s")
    if len(mostrados) > 10:  # Discord admite 10 tarjetas por mensaje: el resto, como líneas
        cabecera += "\n" + "\n".join(_linea_resultado(i, r) for i, r in enumerate(mostrados[10:], 11))
    try:
        await espera.delete()
    except discord.errors.HTTPException:
        pass
    await ctx.send(content=cabecera[:2000], embeds=tarjetas, files=archivos, view=ElegirDescargaView(ctx, mostrados))


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
            ruta = os.path.join(carpeta, os.path.basename(a.filename))
            await a.save(ruta)
            bot.loop.create_task(traducir_y_entregar(ctx.channel, ruta, ctx.author.display_name, borrar_al_final=True,
                                                     solicitante_id=ctx.author.id))
        return

    if enlace:
        enlace = enlace.strip().strip('"').strip("<>")  # comillas o <enlace> de Discord
        ruta = _pdf_de_gofile(enlace) if "gofile.io" in enlace else _find_pdf_fast(os.path.basename(enlace))
        if ruta:
            bot.loop.create_task(traducir_y_entregar(ctx.channel, ruta, ctx.author.display_name,
                                                     solicitante_id=ctx.author.id))
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
