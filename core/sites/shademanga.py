"""
ShadeManga site handler.
"""
import os
import re
import shutil
import uuid
from typing import Callable, Optional
from playwright.async_api import async_playwright

from .base import BaseSiteHandler
from .. import config
from ..utils import finalize_pdf_flow, clean_filename


class ShadeMangaHandler(BaseSiteHandler):
    """Handler for Shademanga.com."""
    
    @staticmethod
    def get_supported_domains() -> list:
        return ["shademanga.com", "shadowmanga.es"]
    
    async def process(
        self,
        url: str,
        log_callback: Callable[[str], None],
        check_cancel: Callable[[], bool],
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> None:
        """
        Download images from Shademanga.com using Playwright to extract DOM
        image URLs, then downloading them directly.
        """
        if "/reader/" not in url:
            log_callback("[ERROR] URL no soportada. Solo se permiten enlaces directos al lector de capítulos (ej. /reader/local/...)")
            return

        log_callback(f"[INIT] Processing ShadeManga URL: {url}...")
        
        current_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        unique_id = uuid.uuid4().hex[:8]
        temp_dir = os.path.join(current_dir, config.TEMP_FOLDER_NAME, f"shademanga_{unique_id}")
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        os.makedirs(temp_dir, exist_ok=True)
        
        download_targets = []
        
        async with async_playwright() as p:
            is_headless = os.getenv("HEADLESS", "false").lower() == "true" or not os.getenv("DISPLAY")
            if os.name == 'nt': 
                is_headless = True
            
            browser = await p.chromium.launch(
                headless=is_headless, 
                args=["--no-sandbox", "--disable-setuid-sandbox", "--start-maximized"]
            )
            context = await browser.new_context(
                user_agent=config.USER_AGENT,
                viewport={'width': 1280, 'height': 720}
            )
            page = await context.new_page()
            
            try:
                log_callback(f"[INFO] Opening reader: {url}")
                await page.goto(url, wait_until="networkidle")
                
                # Extra time for images to populate dynamically
                await page.wait_for_timeout(3000)
                
                html_content = await page.content()
                
                urls = re.findall(r'https://cdn\.shadowmanga\.es/mangas/[a-zA-Z0-9\-\./_]+\.(?:webp|jpg|jpeg|png)', html_content)
                urls = list(dict.fromkeys(urls))
                
                if not urls:
                    log_callback("[ERROR] No se encontraron imágenes del manga en el DOM.")
                    return
                
                total_pages = len(urls)
                log_callback(f"[INFO] Total pages detected: {total_pages}")
                
                title = f"ShadeManga_{unique_id}"
                try:
                    parts = urls[0].split('/')
                    manga_name = parts[-3]
                    chapter = parts[-2]
                    title = f"{manga_name}_ch_{chapter}"
                except Exception:
                    pass
                    
                log_callback(f"[INFO] Deduced Manga Title: {title}")
                
                for i, img_url in enumerate(urls, start=1):
                    if check_cancel():
                        log_callback("[WARN] Process cancelled by user.")
                        break

                    try:
                        log_callback(f"[DEBUG] Fetching page {i}: {img_url}")
                        
                        response = await page.request.get(img_url, headers={"Referer": url})
                        
                        if response.status == 200:
                            data = await response.body()
                            ext = img_url.split('.')[-1]
                            if '?' in ext:
                                ext = ext.split('?')[0]
                                
                            filename = f"{i:03d}.{ext}"
                            filepath = os.path.join(temp_dir, filename)
                            
                            with open(filepath, 'wb') as f:
                                f.write(data)
                            
                            download_targets.append(filepath)
                            
                            if progress_callback:
                                progress_callback(i, total_pages)
                        else:
                            log_callback(f"[ERROR] Page {i} failed with status {response.status}")
                        
                        await page.wait_for_timeout(200)
                        
                    except Exception as e:
                        log_callback(f"[ERROR] Error downloading page {i}: {e}")

                log_callback(f"\n[INFO] Download finished. {len(download_targets)} images retrieved.")
                
            except Exception as e:
                log_callback(f"[ERROR] Playwright global error: {e}")
            finally:
                await browser.close()

        if download_targets:
            pdf_name = f"{clean_filename(title)}.pdf"
            finalize_pdf_flow(
                download_targets, 
                pdf_name, 
                log_callback, 
                temp_dir,
                open_result=config.OPEN_RESULT_ON_FINISH
            )
        else:
            log_callback("[ERROR] No images downloaded for PDF.")
