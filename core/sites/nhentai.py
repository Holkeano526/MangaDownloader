
"""
nhentai.net site handler.
"""
import os
import re
import json
import shutil
from typing import Callable, Optional
from playwright.async_api import async_playwright

from .base import BaseSiteHandler
from .. import config
from ..utils import download_and_make_pdf, clean_filename


class NHentaiHandler(BaseSiteHandler):
    """Handler for nhentai.net website."""
    
    @staticmethod
    def get_supported_domains() -> list:
        return ["nhentai.net"]
    
    async def process(
        self,
        url: str,
        log_callback: Callable[[str], None],
        check_cancel: Callable[[], bool],
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> None:
        """Process nhentai.net URL."""
        id_match = re.search(r'nhentai\.net/g/(\d+)', url)
        if not id_match:
            log_callback("[ERROR] Could not extract ID from URL.")
            return
        gallery_id = id_match.group(1)
        
        log_callback(f"[INIT] Processing nhentai ID: {gallery_id}...")
        
        api_url = f"https://nhentai.net/api/gallery/{gallery_id}"
        
        # Temp dir handling is usually done by download_and_make_pdf, 
        # but nHentai logic creates list of URLs first so we can use that.
        # The original code manually recreated temp dir. 
        # download_and_make_pdf handles temp dir creation/cleanup.
        # So we dont need to do it here unless we download manually.
        # The original code did `download_and_make_pdf` at the end.
        
        images_data = []
        title = f"nhentai_{gallery_id}"
        media_id = ""
    
        async with async_playwright() as p:
            # Determine headless mode
            is_headless = os.getenv("HEADLESS", "false").lower() == "true" or not os.getenv("DISPLAY")
            if os.name == 'nt': is_headless = False
            
            args = [
                "--no-sandbox", 
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled" 
            ]
            
            browser = await p.chromium.launch(headless=is_headless, args=args)
            
            context = await browser.new_context(user_agent=config.USER_AGENT)
            page = await context.new_page()
            
            try:
                log_callback("[INFO] Fetching metadata...")
                await page.goto(api_url, wait_until="domcontentloaded")
                
                # Browser might wrap JSON in PRE tag or just text
                content = await page.inner_text("body")
                
                try:
                    data = json.loads(content)
                    if "title" in data:
                        title = data["title"].get("pretty", data["title"].get("english", title))
                    
                    media_id = data.get("media_id")
                    images_list = data.get("images", {}).get("pages", [])
                    
                    ext_map = {'j': 'jpg', 'p': 'png', 'w': 'webp'}
                    
                    for idx, img in enumerate(images_list):
                        t = img.get('t')
                        ext = ext_map.get(t, 'jpg')
                        # Format: https://i.nhentai.net/galleries/{media_id}/{page_num}.{ext}
                        img_url = f"https://i.nhentai.net/galleries/{media_id}/{idx+1}.{ext}"
                        images_data.append(img_url)
                        
                except json.JSONDecodeError:
                    preview = content[:200] if content else "Empty content"
                    log_callback(f"[ERROR] Invalid JSON. Response: {preview}")
                    return
                    
            except Exception as e:
                log_callback(f"[ERROR] Error fetching metadata: {e}")
                return
            finally:
                await browser.close()
                
        if images_data:
            log_callback(f"[INFO] Gallery: {title} ({len(images_data)} imgs)")
            
            # nhentai usually doesn't need Referer for 'i.nhentai.net' but User-Agent helps
            headers = {"User-Agent": config.USER_AGENT} 
            
            pdf_name = f"{clean_filename(title)}.pdf"
            await download_and_make_pdf(
                images_data, 
                pdf_name, 
                headers, 
                log_callback, 
                check_cancel, 
                progress_callback,
                open_result=config.OPEN_RESULT_ON_FINISH
            )
        else:
            log_callback("[ERROR] No images found.")
