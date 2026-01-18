"""
Hentai2Read site handler.
"""
import re
from typing import Callable, Optional
from crawl4ai import AsyncWebCrawler

from .base import BaseSiteHandler
from ..config import HEADERS_H2R
from ..core.downloader import download_and_make_pdf


class H2RHandler(BaseSiteHandler):
    """Handler for Hentai2Read website."""
    
    @staticmethod
    def get_supported_domains() -> list:
        return ["hentai2read"]
    
    async def process(
        self,
        url: str,
        log_callback: Callable[[str], None],
        check_cancel: Callable[[], bool],
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> None:
        """Process Hentai2Read URL."""
        log_callback("[INIT] Procesando Hentai2Read...")
        
        async with AsyncWebCrawler(verbose=True) as crawler:
            result = await crawler.arun(url=url, bypass_cache=True)
            if not result.success:
                log_callback(f"[ERROR] Page load failed: {result.error_message}")
                return
                
            html = result.html
            
            # Extract gData JSON variable
            gdata_match = re.search(r'var gData\s*=\s*(\{.*?\});', html, re.DOTALL)
            
            if gdata_match:
                try:
                    json_str = gdata_match.group(1)
                    images_match = re.search(r'[\'"]images[\'"]\s*:\s*\[(.*?)\]', json_str, re.DOTALL)
                    if images_match:
                        img_list_raw = images_match.group(1)
                        raw_paths = re.findall(r'["\']([^"\']+)["\']', img_list_raw)
                        paths = [p.replace('\\/', '/') for p in raw_paths]
                        
                        base_url = "https://static.hentai.direct/hentai"
                        cdn_match = re.search(r'src=["\']( https://[^"/]+/hentai)/', html)
                        if cdn_match: 
                            base_url = cdn_match.group(1)
                        
                        image_urls = [f"{base_url}{p}" if not p.startswith("http") else p for p in paths]
                        log_callback(f"[INFO] Se encontraron {len(image_urls)} imágenes.")
                        
                        pdf_name = "hentai2read_chapter.pdf"
                        title_match = re.search(r'[\'"]title[\'"]\s*:\s*[\'"]( .*?)[\'"]', json_str)
                        if title_match:
                            safe = re.sub(r'[\\/*?:"<>|]', "", title_match.group(1).strip())
                            pdf_name = f"{safe}.pdf"
                        
                        await download_and_make_pdf(
                            image_urls,
                            pdf_name,
                            HEADERS_H2R,
                            log_callback,
                            check_cancel,
                            progress_callback=progress_callback
                        )
                    else:
                        log_callback("[ERROR] No se pudo extraer la lista de imágenes.")
                except Exception as e:
                    log_callback(f"[ERROR] Error procesando metadatos: {e}")
            else:
                log_callback("[ERROR] No se encontraron datos del capítulo.")
