
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
import json
import tempfile
import time
from typing import Optional, Dict
from dotenv import load_dotenv

import core
import core.config
import mangascan_client

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
    Redirects core logs to an editable Discord message.
    Automatically detects generated PDFs for upload.
    Uses O(1) indexed lookup instead of recursive os.walk.
    """
    def __init__(self, ctx):
        self.ctx = ctx
        self.message = None
        self.logs = []
        self.last_update_time = 0
        self.update_interval = 2.0 
        self.generated_files = [] 
        self.accumulated_logs = []

    async def initialize(self):
        embed = discord.Embed(title="[+] Starting download...", color=discord.Color.blue())
        self.message = await self.ctx.send(embed=embed)

    def log_callback(self, text: str):
        print(f"[INTERNAL LOG] {text}") 
        self.logs.append(text)
        
        if len(self.logs) > 10:
            self.logs.pop(0)

        # Detect generated PDF from core logs
        # Format: [SUCCESS] PDF Generated: file.pdf
        match = re.search(r"\[SUCCESS\] PDF Generated: (.*)", text)
        if match:
            filename = match.group(1).strip()
            
            if os.path.isabs(filename) and os.path.exists(filename):
                self.generated_files.append(filename)
                print(f"[BOT FILE DETECTED ABS] {filename}")
            else:
                # O(1) indexed lookup instead of recursive os.walk
                result = _find_pdf_fast(filename)
                if result:
                    self.generated_files.append(result)
                    print(f"[BOT FILE DETECTED INDEX] {result}")
                elif os.path.exists(filename):
                    self.generated_files.append(filename)
                    print(f"[BOT FILE DETECTED LOCAL] {filename}")

        import time
        current_time = time.time()
        
        is_urgent = "[DONE]" in text or "[ERROR]" in text
        
        if is_urgent or (current_time - self.last_update_time) > self.update_interval:
            self.last_update_time = current_time
            asyncio.run_coroutine_threadsafe(self.update_discord_message(), bot.loop)

    async def update_discord_message(self):
        if not self.message: return
        
        log_text = "\n".join(self.logs)
        log_text = f"```\n{log_text}\n```"
        
        embed = self.message.embeds[0]
        embed.description = log_text
        
        try:
            await self.message.edit(embed=embed)
        except discord.errors.HTTPException:
            pass 

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


def _barra(actual: int, total: int, ancho: int = 20) -> str:
    if not total:
        return ""
    llenos = round(ancho * actual / total)
    return "█" * llenos + "░" * (ancho - llenos)


async def traducir_y_entregar(channel, ruta_pdf: str, solicitante: str, borrar_al_final: bool = False) -> None:
    """Manda un PDF local a MangaScan, muestra el progreso y entrega el resultado."""
    nombre = os.path.basename(ruta_pdf)
    embed = discord.Embed(title=f"🌐 Traduciendo: {nombre}"[:250], color=discord.Color.purple(),
                          description="Comprobando MangaScan...")
    embed.set_footer(text=f"Pedido por {solicitante}")
    mensaje = await channel.send(embed=embed)
    ultimo = {"t": 0.0, "fase": ""}

    async def actualizar(est: mangascan_client.EstadoTraduccion, forzar: bool = False):
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
        if resultado:
            embed.title = f"[SUCCESS] Traducido: {nombre}"[:250]
            embed.color = discord.Color.green()
            await mensaje.edit(embed=embed)
            await _entregar_pdf(channel, resultado)
        else:
            embed.title = f"[ERROR] No se pudo traducir: {nombre}"[:250]
            embed.color = discord.Color.red()
            if estado.errores:
                embed.add_field(name="Detalle", value=f"```\n{estado.errores[-1][:900]}\n```", inline=False)
            await mensaje.edit(embed=embed)
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
        bot.loop.create_task(traducir_y_entregar(interaction.channel, self.file_path, interaction.user.display_name))

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

    logger = DiscordLogAdapter(ctx)
    await logger.initialize()
    
    stop_event = False
    def check_cancel(): return stop_event

    def progress_adapter(current, total): pass

    try:
        # Use semaphore to allow up to 3 concurrent downloads
        if download_semaphore.locked():
            await ctx.send("⏳ Solicitud recibida. Esperando en cola...")
        else:
            await ctx.send("⏳ Iniciando descarga...")
        async with download_semaphore:
            result = await core.process_entry(
                url, 
                logger.log_callback, 
                check_cancel, 
                progress_callback=progress_adapter
            )
            
        if isinstance(result, list):
            await ctx.send(f"📚 **Serie detectada**. Se encontraron **{len(result)} capítulos**. Añadiéndolos a la cola...")
            for sub_url in result:
                # Disparamos cada capítulo como una solicitud individual
                # Esto creará un mensaje de Discord para cada uno y respetará el semaphore global.
                bot.loop.create_task(ctx.invoke(descargar, url=sub_url))
            return
        
        await asyncio.sleep(1)
        
        embed = logger.message.embeds[0]
        
        if logger.generated_files:
            embed.title = "[SUCCESS] Download Finished"
            embed.color = discord.Color.green()
            await logger.message.edit(embed=embed)
            
            for file_path in logger.generated_files:
                filename = os.path.basename(file_path)
                try:
                    # Discord (8MB safe limit) o GoFile, con botón para traducirlo con MangaScan
                    await _entregar_pdf(ctx.channel, file_path, view=TraducirView(file_path))
                except Exception as e:
                    await ctx.send(f"Error processing file `{filename}`: {e}")
        else:
            embed.title = "[ERROR] Process finished without files"
            embed.color = discord.Color.red()
            await logger.message.edit(embed=embed)

    except Exception as e:
        await ctx.send(f"Critical error: {e}")
        import traceback
        traceback.print_exc()

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
            bot.loop.create_task(traducir_y_entregar(ctx.channel, ruta, ctx.author.display_name, borrar_al_final=True))
        return

    if enlace:
        enlace = enlace.strip().strip('"').strip("<>")  # comillas o <enlace> de Discord
        ruta = _pdf_de_gofile(enlace) if "gofile.io" in enlace else _find_pdf_fast(os.path.basename(enlace))
        if ruta:
            bot.loop.create_task(traducir_y_entregar(ctx.channel, ruta, ctx.author.display_name))
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
