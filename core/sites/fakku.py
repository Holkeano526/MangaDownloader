"""
Fakku.cc (Faccina) site handler.
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
        and image hashes, then downloading them directly.
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
                
                # Extract total pages
                html_content = await page.content()
                total_pages = 0
                
                # Search for "1 / 167" pattern
                pages_match = re.search(r'1\s*/\s*(\d+)', html_content)
                if pages_match:
                    total_pages = int(pages_match.group(1))
                else:
                    log_callback("[ERROR] Could not determine total pages from DOM.")
                    return
                
                log_callback(f"[INFO] Total pages: {total_pages}")
                
                # Extract image base hash
                # Example: src="/image/44274e2779652ba8/1?type=5cfead81"
                img_src_match = re.search(r'src=["\'](/image/[a-fA-F0-9]+)/1\?', html_content)
                if not img_src_match:
                    img_src_match = re.search(r'src=["\'](/image/[a-fA-F0-9]+)/1["\']', html_content)

                if not img_src_match:
                    log_callback("[ERROR] Could not extract image base hash. Page format changed?")
                    return
                    
                base_img_path = img_src_match.group(1) # e.g. "/image/44274e2779652ba8"
                log_callback(f"[INFO] Image base extracted: {base_img_path}")
                
                # Download loop
                for i in range(1, total_pages + 1):
                    if check_cancel():
                        log_callback("[WARN] Process cancelled by user.")
                        break

                    try:
                        img_url = f"https://fakku.cc{base_img_path}/{i}"
                        log_callback(f"[DEBUG] Fetching page {i}: {img_url}")
                        
                        response = await page.request.get(img_url, headers={"Referer": reader_url})
                        
                        if response.status == 200:
                            data = await response.body()
                            ext = "jpg" # Faccina usually returns images without extension in URL
                            
                            # Deduce extension from content-type if available
                            ct = response.headers.get("content-type", "")
                            if "png" in ct: ext = "png"
                            elif "webp" in ct: ext = "webp"
                            elif "gif" in ct: ext = "gif"
                            
                            filename = f"{i:03d}.{ext}"
                            filepath = os.path.join(temp_dir, filename)
                            
                            with open(filepath, 'wb') as f:
                                f.write(data)
                            
                            download_targets.append(filepath)
                            
                            if progress_callback:
                                progress_callback(i, total_pages)
                        else:
                            log_callback(f"[ERROR] Page {i} failed with status {response.status}")
                        
                        # Be gentle with the server
                        await page.wait_for_timeout(200)
                        
                    except Exception as e:
                        log_callback(f"[ERROR] Error downloading page {i}: {e}")

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
