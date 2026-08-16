
"""
Module: bot
Description: A Discord API integration (Discord Bot) for the Manga Downloader.
It allows users to trigger downloads via Discord commands (e.g., !descargar <url>).
The bot features live-updating embed messages for progress logs and automatically 
uploads files exceeding Discord's 8MB limit to GoFile for easy user retrieval.
"""
import discord
from discord.ext import commands
import os
import asyncio
import re
import aiohttp
from typing import Optional, Dict
from dotenv import load_dotenv

import core
import core.config

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
                try:
                    filename = os.path.basename(file_path)
                    size_mb = os.path.getsize(file_path) / (1024 * 1024)

                    # Discord Limit check (8MB Safe limit)
                    if size_mb > 7.9: 
                        await ctx.send(f"[WARN] `{filename}` ({size_mb:.2f}MB) exceeds limit. Uploading to GoFile...")
                        
                        link = await upload_to_gofile(file_path)
                        if link:
                            await ctx.send(f"[SUCCESS] **{filename}**: {link}")
                        else:
                            await ctx.send(f"[ERROR] Failed to upload `{filename}` to GoFile.")
                    else:
                        await ctx.send(file=discord.File(file_path))
                        
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

if __name__ == "__main__":
    if not TOKEN:
        print("ERROR: DISCORD_TOKEN not found in .env")
    else:
        bot.run(TOKEN)
