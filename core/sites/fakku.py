"""
Fakku.cc (Faccina) site handler.
"""
import asyncio
import os
import re
import shutil
import sys
import uuid
from typing import Callable, Optional
from playwright.async_api import async_playwright

from .base import BaseSiteHandler
from .. import config
from ..utils import clean_filename

# Semáforo para limitar descargas concurrentes y evitar 429 (Too Many Requests)
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(5)


class FakkuHandler(BaseSiteHandler):
    """Handler for Fakku.cc (Faccina)."""
    
    @staticmethod
    def get_supported_domains() -> list:
        return ["fakku.cc"]
    
    async def process(
        self,
        url: str,
        log_callback: Callable[[str], None],
        check_cancel: Callable[[], bool],
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> None:
        """
        Download images from Fakku.cc using Playwright to extract total pages
        and image hashes, then downloading them in parallel with a semaphore.
        """
        id_match = re.search(r'/g/(\d+)', url)
        if not id_match:
            log_callback("[ERROR] Could not extract Gallery ID from URL. URL must contain /g/ID")
            return
        
        gallery_id = id_match.group(1)
        
        log_callback(f"[INIT] Processing Fakku ID: {gallery_id}...")
        
        # Create temp directory
        current_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        unique_id = uuid.uuid4().hex[:8]
        temp_dir = os.path.join(current_dir, config.TEMP_FOLDER_NAME, f"fakku_{gallery_id}_{unique_id}")
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        os.makedirs(temp_dir, exist_ok=True)
        
        download_targets = []
        
        async with async_playwright() as p:
            is_headless = os.getenv("HEADLESS", "false").lower() == "true" or not os.getenv("DISPLAY")
            if os.name == 'nt': 
                # Run headless anyway on Windows unless user is debugging? 
                # Let's use headless True to avoid annoying popups, 
                # Cloudflare didn't block headless in our test
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
                reader_url = f"https://fakku.cc/g/{gallery_id}/read/1"
                log_callback(f"[INFO] Opening reader: {reader_url}")
                await page.goto(reader_url, wait_until="domcontentloaded")
                
                # Give it time to bypass Cloudflare if needed and load DOM
                await page.wait_for_timeout(3000)
                
                page_title = await page.title()
                
                title = f"Fakku_{gallery_id}"
                # The title format is usually: "Page 1 - <Manga Title> - faccina"
                title_match = re.search(r'Page 1 - (.*?) - faccina', page_title, re.IGNORECASE)
                if title_match:
                    clean_title = re.sub(r'[\\/*?:"<>|]', '', title_match.group(1)).strip()
                    title = clean_title if clean_title else title
                else:
                    clean_title = re.sub(r'[\\/*?:"<>|]', '', page_title).strip()
                    title = clean_title if clean_title else title
                log_callback(f"[INFO] Detected Manga Title: {title}")
                
                # --- Extract total pages using Playwright DOM (preferred) with regex fallback ---
                total_pages = 0
                try:
                    # Intentar ubicar el contador de páginas vía selector de Playwright
                    page_counter = page.locator("text=/\\d+\\s*/\\s*\\d+/").first
                    counter_text = await page_counter.inner_text()
                    parts = counter_text.split('/')
                    if len(parts) == 2:
                        total_pages = int(parts[1].strip())
                        log_callback(f"[INFO] Total pages extracted via DOM: {total_pages}")
                except Exception:
                    log_callback("[WARN] DOM page counter not found. Falling back to regex...")
                
                if not total_pages:
                    # Fallback: regex sobre el HTML (legado)
                    html_content = await page.content()
                    pages_match = re.search(r'>\s*1\s*/\s*(\d+)\s*<', html_content)
                    if pages_match:
                        total_pages = int(pages_match.group(1))
                        log_callback(f"[INFO] Total pages extracted via regex fallback: {total_pages}")
                    else:
                        log_callback("[ERROR] Could not determine total pages from DOM or regex. Possible site layout change.")
                        return
                
                log_callback(f"[INFO] Total pages: {total_pages}")
                
                # --- Extract image base hash using Playwright DOM (preferred) with regex fallback ---
                base_img_path = None
                try:
                    img_element = page.locator("img[src*='/image/']").first
                    img_src = await img_element.get_attribute("src")
                    if img_src:
                        # Ejemplo: "/image/44274e2779652ba8/1?type=5cfead81" o "/image/44274e2779652ba8/1"
                        hash_match = re.search(r'(/image/[a-fA-F0-9]+)/', img_src)
                        if hash_match:
                            base_img_path = hash_match.group(1)
                            log_callback(f"[INFO] Image base extracted via DOM: {base_img_path}")
                except Exception:
                    log_callback("[WARN] DOM image selector failed. Falling back to regex...")
                
                if not base_img_path:
                    # Fallback: regex sobre el HTML (legado)
                    html_content = await page.content()
                    img_src_match = re.search(r'src=["\'](/image/[a-fA-F0-9]+)/1\?', html_content)
                    if not img_src_match:
                        img_src_match = re.search(r'src=["\'](/image/[a-fA-F0-9]+)/1["\']', html_content)
                    if img_src_match:
                        base_img_path = img_src_match.group(1)
                        log_callback(f"[INFO] Image base extracted via regex fallback: {base_img_path}")
                    else:
                        log_callback("[ERROR] Could not extract image base hash. Page format changed?")
                        return
                
                # --- Parallel download with Semaphore (max 5 concurrent requests) ---
                completed_count = [0]  # mutable counter for progress tracking
                
                async def download_page(i: int) -> Optional[str]:
                    async with DOWNLOAD_SEMAPHORE:
                        if check_cancel():
                            return None
                        try:
                            img_url = f"https://fakku.cc{base_img_path}/{i}"
                            response = await page.request.get(img_url, headers={"Referer": reader_url})
                            
                            if response.status == 200:
                                data = await response.body()
                                ext = "jpg"
                                ct = response.headers.get("content-type", "")
                                if "png" in ct: ext = "png"
                                elif "webp" in ct: ext = "webp"
                                elif "gif" in ct: ext = "gif"
                                
                                filename = f"{i:03d}.{ext}"
                                filepath = os.path.join(temp_dir, filename)
                                
                                # Write file using asyncio.to_thread to avoid blocking event loop
                                def _write_file():
                                    with open(filepath, 'wb') as f:
                                        f.write(data)
                                await asyncio.to_thread(_write_file)
                                
                                completed_count[0] += 1
                                if progress_callback:
                                    progress_callback(completed_count[0], total_pages)
                                return filepath
                            else:
                                log_callback(f"[ERROR] Page {i} failed with status {response.status}")
                                return None
                        except Exception as e:
                            log_callback(f"[ERROR] Error downloading page {i}: {e}")
                            return None
                
                # Create all download tasks and run them concurrently
                tasks = [download_page(i) for i in range(1, total_pages + 1)]
                results = await asyncio.gather(*tasks)
                download_targets = [r for r in results if r is not None]
                
                log_callback(f"\n[INFO] Download finished. {len(download_targets)} images retrieved.")
                
            except Exception as e:
                log_callback(f"[ERROR] Playwright global error: {e}")
            finally:
                await browser.close()

        # Generate PDF via Shared Utils (async version to not block event loop)
        if download_targets:
            from ..utils import async_finalize_pdf_flow
            pdf_name = f"{clean_filename(title)}.pdf"
            await async_finalize_pdf_flow(
                download_targets, 
                pdf_name, 
                log_callback, 
                temp_dir,
                open_result=config.OPEN_RESULT_ON_FINISH
            )
        else:
            log_callback("[ERROR] No images downloaded for PDF.")
