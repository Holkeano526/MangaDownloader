import discord
from discord.ext import commands
import os
import asyncio
import re
import aiohttp
from typing import Optional
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# ==============================================================================
# IMPORTACIÓN Y CONFIGURACIÓN DEL CORE
# ==============================================================================
import core

# Configurar el core para MODO BOT (Silencioso)
core.OPEN_RESULT_ON_FINISH = False

# ==============================================================================
# CONFIGURACIÓN DEL BOT
# ==============================================================================
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

# ==============================================================================
# CLASE DE UTILIDAD PARA LOGS
# ==============================================================================
class DiscordLogAdapter:
    """
    Redirige los logs del script tmo.py a un mensaje de Discord editable.
    Detecta automáticamente cuando se genera un PDF para subirlo.
    """
    def __init__(self, ctx):
        self.ctx = ctx
        self.message = None
        self.logs = []
        self.last_update_time = 0
        self.update_interval = 2.0  # Segundos entre actualizaciones para evitar Rate Limit
        self.generated_files = [] 
        self.accumulated_logs = []

    async def initialize(self):
        """Envía el mensaje inicial."""
        embed = discord.Embed(title="⏳ Iniciando descarga...", color=discord.Color.blue())
        self.message = await self.ctx.send(embed=embed)

    def log_callback(self, text: str):
        """Esta función se pasa a tmo.process_entry"""
        print(f"[LOG INTERNO] {text}") # Mantener log en consola del bot
        self.logs.append(text)
        
        # Mantener solo los últimos 10 logs visibles
        if len(self.logs) > 10:
            self.logs.pop(0)

        # Detectar PDF generado
        # Detectar PDF Generado (Core.py standard format)
        # [EXITO] PDF Generado: archivo.pdf
        match = re.search(r"\[EXITO\] PDF Generado: (.*)", text)
        if match:
            filename = match.group(1).strip()
            # Buscar en carpeta PDF relativa al core
            current_dir = os.path.dirname(os.path.abspath(core.__file__))
            
            # Intento 1: Ruta exacta si el core devolvió ruta absoluta (a veces pasa)
            if os.path.isabs(filename) and os.path.exists(filename):
                self.generated_files.append(filename)
                print(f"[BOT FILE DETECTED ABS] {filename}")
            else:
                # Intento 2: Buscar recursivamente en carpeta PDF por si está en subcarpeta (Manga/Capitulo.pdf)
                pdf_root = os.path.join(current_dir, core.PDF_FOLDER_NAME)
                found = False
                
                # Check direct match
                direct_path = os.path.join(pdf_root, filename)
                if os.path.exists(direct_path):
                    self.generated_files.append(direct_path)
                    print(f"[BOT FILE DETECTED DIR] {direct_path}")
                    found = True
                
                # Check recursive search if not found directly (for subfolders like ZonaTMO/MangaName)
                if not found:
                    for root, dirs, files in os.walk(pdf_root):
                        if filename in files:
                            full_path = os.path.join(root, filename)
                            self.generated_files.append(full_path)
                            print(f"[BOT FILE DETECTED REC] {full_path}")
                            # Si hay multiples con mismo nombre, agarramos el primero (lo mas reciente suele ser)
                            # O podríamos agregar todos. Por seguridad, agregamos este y seguimos.
                            found = True
                            
                if not found:
                     # Fallback: maybe it is just a filename locally?
                     if os.path.exists(filename):
                         self.generated_files.append(filename)
                         print(f"[BOT FILE DETECTED LOCAL] {filename}")

        # Rate Limiting: Solo actualizar si pasó el tiempo o es un mensaje crítico
        import time
        current_time = time.time()
        
        # Actualizar inmediatamente si es el final o un error grave
        is_urgent = "[HECHO]" in text or "[ERROR]" in text
        
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
            pass # Rate limit ignorado temporalmente

# ==============================================================================
# HELPERS
# ==============================================================================

async def upload_to_gofile(file_path: str) -> Optional[str]:
    """Suba un archivo a GoFile y retorna el link de descarga."""
    try:
        async with aiohttp.ClientSession() as session:
            # 1. Obtener mejor servidor
            async with session.get('https://api.gofile.io/servers') as resp:
                if resp.status != 200: return None
                data = await resp.json()
                if data['status'] != 'ok': return None
                
                server = data['data']['servers'][0]['name']
                upload_url = f'https://{server}.gofile.io/uploadFile'
            
            # 2. Subir archivo
            filename = os.path.basename(file_path)
            with open(file_path, 'rb') as f:
                form_data = aiohttp.FormData()
                form_data.add_field('file', f, filename=filename, content_type='application/pdf')
                
                async with session.post(upload_url, data=form_data) as upload_resp:
                    if upload_resp.status == 200:
                        res = await upload_resp.json()
                        if res['status'] == 'ok':
                            return res['data']['downloadPage']
    except Exception as e:
        print(f"[ERROR GOFILE] {e}")
    return None

# ==============================================================================
# COMANDOS DEL BOT
# ==============================================================================

@bot.event
async def on_ready():
    print(f'Bot conectado como {bot.user}')

@bot.command(name='descargar')
async def descargar(ctx, url: str):
    """
    Descarga un manga desde una URL soportada.
    Uso: !descargar <url>
    """
    if not url:
        await ctx.send("Por favor proporciona una URL.")
        return

    logger = DiscordLogAdapter(ctx)
    await logger.initialize()
    
    stop_event = False
    def check_cancel(): return stop_event

    # Adaptador de progreso vacío (el bot no muestra barra de progreso fina)
    def progress_adapter(current, total): pass

    try:
        await core.process_entry(
            url, 
            logger.log_callback, 
            check_cancel, 
            progress_callback=progress_adapter
        )
        
        # Esperar brevemente para sincronizar logs
        await asyncio.sleep(1)
        
        embed = logger.message.embeds[0]
        
        if logger.generated_files:
            embed.title = "✅ Descarga Finalizada"
            embed.color = discord.Color.green()
            await logger.message.edit(embed=embed)
            
            for file_path in logger.generated_files:
                try:
                    filename = os.path.basename(file_path)
                    size_mb = os.path.getsize(file_path) / (1024 * 1024)

                    # Discord Limit check (8MB Safe limit)
                    if size_mb > 7.9: 
                        await ctx.send(f"⚠️ `{filename}` ({size_mb:.2f}MB) excede el límite. Subiendo a GoFile...")
                        
                        link = await upload_to_gofile(file_path)
                        if link:
                            await ctx.send(f"✅ **{filename}**: {link}")
                        else:
                            await ctx.send(f"❌ Error al subir `{filename}` a GoFile.")
                    else:
                        await ctx.send(file=discord.File(file_path))
                        
                except Exception as e:
                    await ctx.send(f"Error procesando archivo `{filename}`: {e}")
        else:
            embed.title = "❌ Proceso terminado sin archivos"
            embed.color = discord.Color.red()
            await logger.message.edit(embed=embed)

    except Exception as e:
        await ctx.send(f"Ocurrió un error crítico: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    if not TOKEN:
        print("ERROR: No se encontró el DISCORD_TOKEN en .env")
    else:
        bot.run(TOKEN)
