
"""
M440/Mangas.in site handler.
"""
import os
import re
from typing import Callable, Optional
from crawl4ai import AsyncWebCrawler

from .base import BaseSiteHandler
from .. import config
from ..utils import download_and_make_pdf, clean_filename


class M440Handler(BaseSiteHandler):
    """Handler for M440.in website."""
    
    @staticmethod
    def get_supported_domains() -> list:
        return ["m440.in", "mangas.in"]
    
    async def process(
        self,
        url: str,
        log_callback: Callable[[str], None],
        check_cancel: Callable[[], bool],
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> None:
        """Process M440.in URL."""
        log_callback("[INIT] Processing M440...")

        async with AsyncWebCrawler(verbose=True) as crawler:
            result = await crawler.arun(url=url, bypass_cache=True)
            if not result.success:
                log_callback(f"[ERROR] Page load failed: {result.error_message}")
                return

            html = result.html
            clean_url = url.split("?")[0].rstrip("/")
            is_cover_page = bool(re.search(r'/manga/[^/]+$', clean_url))
            
            if not is_cover_page:
                 potential_chapters = re.findall(r'href=["\'](https://m440.in/manga/[^/]+/[^"\']+)["\']', html)
                 if len(set(potential_chapters)) > 3: is_cover_page = True

            if is_cover_page:
                log_callback("[INFO] Cover detected. Extracting chapters...")
                manga_title = "Manga_M440"
                title_match = re.search(r'<h2[^>]*class=["\']widget-title["\'][^>]*>(.*?)</h2>', html)
                if title_match: 
                    manga_title = clean_filename(title_match.group(1).strip())
                
                links = re.findall(r'href=["\'](https://m440.in/manga/[^/]+/[^"\']+)["\']', html)
                seen = set()
                clean_links = []
                for l in links:
                    if l not in seen and "/manga/" in l and l != url:
                        seen.add(l)
                        clean_links.append(l)
                clean_links.reverse() 
                
                if not clean_links:
                    log_callback("[ERROR] No chapters found on cover.")
                    return

                # Determine output directory
                # Using config.PDF_FOLDER_NAME relative to CWD (usually project root)
                pdf_dir = os.path.join(os.getcwd(), config.PDF_FOLDER_NAME, manga_title)
                os.makedirs(pdf_dir, exist_ok=True)

                for i, chap_url in enumerate(clean_links):
                    if check_cancel and check_cancel(): break
                    if progress_callback: progress_callback(i + 1, len(clean_links))
                    log_callback(f"Processing Cap {i+1}/{len(clean_links)}")
                    
                    pdf_name = f"{manga_title} - {chap_url.split('/')[-1]}.pdf"
                    full_pdf_path = os.path.join(pdf_dir, pdf_name)
                    
                    if os.path.exists(full_pdf_path): continue
                    
                    await self._process_chapter(chap_url, full_pdf_path, crawler, log_callback, check_cancel, None)
                
                if config.OPEN_RESULT_ON_FINISH:
                    try: os.startfile(pdf_dir)
                    except: pass
            else:
                log_callback("[INFO] Single chapter detected.")
                pdf_name = "m440_chapter.pdf"
                
                # Single chapter output
                await self._process_chapter(
                    url, 
                    pdf_name, # Relative path (defaults to PDF folder in utils) or handle absolute below
                    crawler, 
                    log_callback, 
                    check_cancel, 
                    progress_callback
                )
                
                # Opening is handled by download_and_make_pdf mostly, but process_m440_chapter logic
                # in original code called download_and_make_pdf with is_path=True if passing absolute path
                # For single chapter, original code passed "m440_chapter.pdf" (relative) 
                # and then manually opened it. 
                # Our new download_and_make_pdf handles opening if open_result=True.
                
                # However, original code for single chapter:
                # full_pdf_path = os.path.join(pdf_dir, pdf_name)
                # await process_m440_chapter(input_url, full_pdf_path, ...)
                # So it passed absolute path.

    async def _process_chapter(
        self, 
        url: str, 
        output_pdf_path: str, 
        crawler: AsyncWebCrawler, 
        log_callback: Callable[[str], None], 
        check_cancel: Callable[[], bool], 
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> None:
        """Helper to process a single chapter."""
        
        # Determine if output_pdf_path is absolute or relative
        is_path = "/" in output_pdf_path or "\\" in output_pdf_path
        
        # If valid path and not exists (checked by caller for mass download, but single chap might check here)
        # But we delegate to download_and_make_pdf
        
        result = await crawler.arun(url=url, bypass_cache=True)
        if not result.success: return
        html = result.html
        matches = re.findall(r'data-src=["\'](https://[^"\']+)["\']', html)
        if matches:
            images = list(dict.fromkeys(matches))
            log_callback(f"[INFO] Downloading {len(images)} images...")
            await download_and_make_pdf(
                images, 
                output_pdf_path, 
                config.HEADERS_M440, 
                log_callback, 
                check_cancel, 
                progress_callback, 
                is_path=is_path,
                open_result=config.OPEN_RESULT_ON_FINISH
            )
