
"""
Hitomi.la site handler (Stealth Mode).
"""
import os
import re
import shutil
from typing import Callable, Optional
from playwright.async_api import async_playwright

from .base import BaseSiteHandler
from .. import config
from ..utils import finalize_pdf_flow, clean_filename


class HitomiHandler(BaseSiteHandler):
    """Handler for Hitomi.la website."""
    
    @staticmethod
    def get_supported_domains() -> list:
        return ["hitomi.la"]
    
    async def process(
        self,
        url: str,
        log_callback: Callable[[str], None],
        check_cancel: Callable[[], bool],
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> None:
        """
        Download images from Hitomi using Playwright to simulate a real user
        and obtain high-quality images (page-by-page).
        """
        id_match = re.search(r'[-/](\d+)\.html', url)
        if not id_match:
            log_callback("[ERROR] Could not extract ID from URL.")
            return
        gallery_id = int(id_match.group(1)) # Integer for logic
        
        log_callback(f"[INIT] Processing Hitomi ID: {gallery_id} (Browser Mode)...")
        
        # Create temp directory
        current_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        temp_dir = os.path.join(current_dir, config.TEMP_FOLDER_NAME)
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        os.makedirs(temp_dir, exist_ok=True)
        
        download_targets = []
        
        async with async_playwright() as p:
            # Determine headless mode
            is_headless = os.getenv("HEADLESS", "false").lower() == "true" or not os.getenv("DISPLAY")
            if os.name == 'nt': is_headless = False
            
            # Launch visible browser to behave exactly like a user
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
                # Navigate to Reader
                reader_url = f"https://hitomi.la/reader/{gallery_id}.html#1"
                log_callback(f"[INFO] Opening reader: {reader_url}")
                await page.goto(reader_url, wait_until="domcontentloaded")
                
                # Wait for title
                await page.wait_for_timeout(2000)
                
                title = f"Hitomi_{gallery_id}"
                page_title = await page.title()
                if page_title:
                    clean_title = re.sub(r'[\\/*?:"<>|]', '', page_title).strip()
                    title = clean_title if clean_title else title
                log_callback(f"[INFO] Title detected: {title}")

                # Get total images
                total_images = await page.evaluate("() => window.galleryinfo ? window.galleryinfo.files.length : 0")
                
                if total_images == 0:
                    log_callback("[INFO] 'galleryinfo' not detected, trying fallback...")
                    try:
                        await page.wait_for_function("() => window.galleryinfo && window.galleryinfo.files.length > 0", timeout=5000)
                        total_images = await page.evaluate("() => window.galleryinfo.files.length")
                    except:
                        log_callback("[WARN] Could not determine total images. Estimating...")
                        total_images = 9999 # Arbitrary limit

                log_callback(f"[INFO] Estimated images: {total_images}")

                # Loop through pages
                for i in range(1, total_images + 1):
                    if check_cancel():
                        log_callback("[WARN] Process cancelled by user.")
                        break

                    try:
                        # Update hash to go to next image
                        await page.evaluate(f"location.hash = '#{i}'")
                        
                        # Wait for image to update
                        selector = "div#comicImages img" 
                        await page.wait_for_function(
                            """(selector) => {
                                const img = document.querySelector(selector);
                                return img && img.src && img.src.indexOf('http') === 0;
                            }""", 
                            arg=selector, 
                            timeout=10000
                        )
                        
                        # Extract info
                        img_info = await page.evaluate("""(selector) => {
                            const img = document.querySelector(selector);
                            return {src: img.src, width: img.naturalWidth, height: img.naturalHeight};
                        }""", selector)
                        
                        img_src = img_info['src']
                        log_callback(f"[DEBUG] Page {i}: {img_src.split('/')[-1]} ({img_info['width']}x{img_info['height']})")
                        
                        # Download using Page Context with Explicit Referer
                        headers = {"Referer": f"https://hitomi.la/reader/{gallery_id}.html"}
                        response = await page.request.get(img_src, headers=headers)
                        
                        if response.status == 200:
                            data = await response.body()
                            ext = img_src.split('.')[-1]
                            if '?' in ext: ext = ext.split('?')[0]
                            filename = f"{i:03d}.{ext}"
                            filepath = os.path.join(temp_dir, filename)
                            
                            with open(filepath, 'wb') as f:
                                f.write(data)
                            
                            download_targets.append(filepath)
                            log_callback(f"[OK] Downloaded {i}/{total_images}")
                            if progress_callback:
                                progress_callback(i, total_images)
                        else:
                            log_callback(f"[ERROR] Page {i} failed with status {response.status}")
                        
                        await page.wait_for_timeout(500)
                        
                    except Exception as e:
                        log_callback(f"[ERROR] Error on page {i}: {e}")
                        if total_images == 9999 and i > 5:
                            log_callback("[INFO] Possible end of gallery.")
                            break

                log_callback(f"\n[INFO] Download finished. {len(download_targets)} images retrieved.")
                
            except Exception as e:
                log_callback(f"[ERROR] Playwright global error: {e}")
            finally:
                await browser.close()

        # Generate PDF via Shared Utils
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
