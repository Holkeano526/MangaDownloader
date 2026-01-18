"""
M440.in site handler.
"""
import os
import re
from typing import Callable, Optional
from crawl4ai import AsyncWebCrawler

from .base import BaseSiteHandler
from ..config import HEADERS_M440, PDF_FOLDER_NAME
from ..core.downloader import download_and_make_pdf


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
        log_callback("[INIT] Procesando M440...")

        async with AsyncWebCrawler(verbose=True) as crawler:
            result = await crawler.arun(url=url, bypass_cache=True)
            if not result.success:
                log_callback(f"[ERROR] Carga de página falló: {result.error_message}")
                return

            html = result.html
            
            # Detect functionality based on URL structure
            clean_url = url.split("?")[0].rstrip("/")
            is_cover_page = bool(re.search(r'/manga/[^/]+$', clean_url))
            
            # Backup check: Look for multiple chapter links if URL is ambiguous
            if not is_cover_page:
                 potential_chapters = re.findall(r'href=["\']( https://m440.in/manga/[^/]+/[^"\']+)["\']', html)
                 if len(set(potential_chapters)) > 3:
                     is_cover_page = True

            if is_cover_page:
                await self._process_cover(url, html, crawler, log_callback, check_cancel, progress_callback)
            else:
                await self._process_single_chapter(url, crawler, log_callback, check_cancel, progress_callback)
    
    async def _process_cover(
        self,
        url: str,
        html: str,
        crawler: AsyncWebCrawler,
        log_callback: Callable[[str], None],
        check_cancel: Callable[[], bool],
        progress_callback: Optional[Callable[[int, int], None]]
    ) -> None:
        """Process manga cover page with multiple chapters."""
        log_callback("[INFO] Portada detectada. Extrayendo capítulos...")
        manga_title = "Manga_M440"
        title_match = re.search(r'<h2[^>]*class=["\']widget-title["\'][^>]*>(.*?)</h2>', html)
        if title_match:
            manga_title = re.sub(r'[\\/*?:"<>|]', "", title_match.group(1).strip())
        
        links = re.findall(r'href=["\']( https://m440.in/manga/[^/]+/[^"\']+)["\']', html)
        seen = set()
        clean_links = []
        for l in links:
            if l not in seen and "/manga/" in l and l != url:
                seen.add(l)
                clean_links.append(l)
        clean_links.reverse() 
        
        if not clean_links:
            log_callback("[ERROR] No se encontraron capítulos en la portada.")
            return

        current_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        manga_dir = os.path.join(current_dir, "output", PDF_FOLDER_NAME, manga_title)
        os.makedirs(manga_dir, exist_ok=True)

        for i, chap_url in enumerate(clean_links):
            if check_cancel():
                log_callback("[AVISO] Proceso cancelado por el usuario.")
                break
            
            if progress_callback: 
                progress_callback(i + 1, len(clean_links))
            log_callback(f"Procesando Cap {i+1}/{len(clean_links)}")
            pdf_name = f"{manga_title} - {chap_url.split('/')[-1]}.pdf"
            full_pdf_path = os.path.join(manga_dir, pdf_name)
            if os.path.exists(full_pdf_path): 
                continue
            # Pass None for progress because we track chapters here
            await self._process_chapter(chap_url, full_pdf_path, crawler, log_callback, check_cancel, None)
        
        try:
            os.startfile(manga_dir)
        except:
            pass
    
    async def _process_single_chapter(
        self,
        url: str,
        crawler: AsyncWebCrawler,
        log_callback: Callable[[str], None],
        check_cancel: Callable[[], bool],
        progress_callback: Optional[Callable[[int, int], None]]
    ) -> None:
        """Process single chapter URL."""
        log_callback("[INFO] Capítulo individual detectado.")
        pdf_name = "m440_chapter.pdf"
        current_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        pdf_dir = os.path.join(current_dir, "output", PDF_FOLDER_NAME)
        os.makedirs(pdf_dir, exist_ok=True)
        
        full_pdf_path = os.path.join(pdf_dir, pdf_name)
        await self._process_chapter(url, full_pdf_path, crawler, log_callback, check_cancel, progress_callback)
        
        if os.path.exists(full_pdf_path): 
            try: 
                os.startfile(os.path.dirname(full_pdf_path))
            except: 
                pass
            try: 
                os.startfile(full_pdf_path)
            except: 
                pass
    
    async def _process_chapter(
        self,
        url: str,
        output_pdf_path: str,
        crawler: AsyncWebCrawler,
        log_callback: Callable[[str], None],
        check_cancel: Callable[[], bool],
        progress_callback: Optional[Callable[[int, int], None]]
    ) -> None:
        """Download images from a single chapter."""
        result = await crawler.arun(url=url, bypass_cache=True)
        if not result.success: 
            return
        html = result.html
        matches = re.findall(r'data-src=["\']( https://[^"\']+)["\']', html)
        if matches:
            images = list(dict.fromkeys(matches))
            log_callback(f"[INFO] Descargando {len(images)} imágenes...")
            await download_and_make_pdf(
                images,
                output_pdf_path,
                HEADERS_M440,
                log_callback,
                check_cancel,
                progress_callback=progress_callback,
                is_path=True
            )
