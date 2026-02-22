
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
from typing import Optional
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

class DiscordLogAdapter:
    """
    Redirects core logs to an editable Discord message.
    Automatically detects generated PDFs for upload.
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
            # Use CWD for PDF folder resolution as utils writes to CWD/PDF
            current_dir = os.getcwd() 
            
            if os.path.isabs(filename) and os.path.exists(filename):
                self.generated_files.append(filename)
                print(f"[BOT FILE DETECTED ABS] {filename}")
            else:
                pdf_root = os.path.join(current_dir, core.config.PDF_FOLDER_NAME)
                found = False
                
                direct_path = os.path.join(pdf_root, filename)
                if os.path.exists(direct_path):
                    self.generated_files.append(direct_path)
                    print(f"[BOT FILE DETECTED DIR] {direct_path}")
                    found = True
                
                if not found:
                    for root, dirs, files in os.walk(pdf_root):
                        if filename in files:
                            full_path = os.path.join(root, filename)
                            self.generated_files.append(full_path)
                            print(f"[BOT FILE DETECTED REC] {full_path}")
                            found = True
                            
                if not found:
                     if os.path.exists(filename):
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

async def upload_to_gofile(file_path: str) -> Optional[str]:
    """Uploads a file to GoFile and returns the download link."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get('https://api.gofile.io/servers') as resp:
                if resp.status != 200: return None
                data = await resp.json()
                if data['status'] != 'ok': return None
                
                server = data['data']['servers'][0]['name']
                upload_url = f'https://{server}.gofile.io/uploadFile'
            
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
        await core.process_entry(
            url, 
            logger.log_callback, 
            check_cancel, 
            progress_callback=progress_adapter
        )
        
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
