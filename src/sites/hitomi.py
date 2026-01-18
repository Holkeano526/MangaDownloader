"""
Hitomi.la site handler.
"""
import os
import re
import json
import base64
import shutil
from typing import Callable, Optional
import aiohttp
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

from .base import BaseSiteHandler
from ..config import HEADERS_HITOMI, USER_AGENT, DEFAULT_PAGE_COUNT, TEMP_FOLDER_NAME, PDF_FOLDER_NAME
from ..core.pdf_creator import create_pdf


class HitomiHandler(BaseSiteHandler):
    """Handler for Hitomi.la website using stealth mode."""
    
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
        Process Hitomi.la gallery using browser screenshots.
        Uses stealth mode to bypass anti-bot protections.
        """
        log_callback("[INIT] Procesando Hitomi.la (Modo Sigiloso)...")
        
        # Extract Gallery ID
        id_match = re.search(r'[-/](\d+)\.html', url)
        if not id_match:
            log_callback("[ERROR] No se pudo encontrar el ID de la galería en la URL.")
            return
        gallery_id = id_match.group(1)
        
        # Fetch metadata
        total_images, title = await self._fetch_metadata(gallery_id, log_callback)
        
        # Prepare directories
        current_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        temp_folder = os.path.join(current_dir, TEMP_FOLDER_NAME)
        if os.path.exists(temp_folder): 
            shutil.rmtree(temp_folder)
        os.makedirs(temp_folder, exist_ok=True)
        
        files_downloaded = []
        
        # Configure Browser
        browser_cfg = BrowserConfig(
            browser_type="chromium",
            headless=True,
            viewport_width=1920,
            viewport_height=1080,
            headers={
                "User-Agent": USER_AGENT,
                "Referer": f"https://hitomi.la/reader/{gallery_id}.html"
            }
        )

        js_prep = """
        (async () => {
            const sleep = (ms) => new Promise(r => setTimeout(r, ms));
            let styles = `
                body { background: #000 !important; margin: 0 !important; overflow: hidden !important; }
                .navbar, .mobile-navbar, .toggles, #top-nav { display: none !important; }
                #comicImages img { 
                    display: block !important; 
                    margin: 0 auto !important; 
                    max-width: 100vw !important; 
                    max-height: 100vh !important;
                    object-fit: contain !important;
                }
            `;
            let s = document.createElement('style');
            s.innerHTML = styles;
            document.head.appendChild(s);
            
            let retries = 0;
            while(retries < 100) {
                 let img = document.querySelector('#comicImages img'); 
                 if(img && img.naturalWidth > 10 && img.complete) return;
                 await sleep(100);
                 retries++;
            }
        })();
        """

        log_callback("[INFO] Iniciando Sesión de Navegador Persistente...")

        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            for i in range(1, total_images + 1):
                if check_cancel():
                    log_callback("[AVISO] Proceso cancelado por el usuario.")
                    break
                
                if progress_callback: 
                    progress_callback(i, total_images)
                if i % 5 == 0: 
                    log_callback(f"[INFO] Capturando {i}/{total_images}")
                
                page_url = f"https://hitomi.la/reader/{gallery_id}.html#{i}"
                
                run_config = CrawlerRunConfig(
                    js_code=js_prep,
                    screenshot=True,
                    cache_mode=CacheMode.BYPASS,
                    wait_for="#comicImages img",
                    magic=True
                )
                
                result = await crawler.arun(page_url, config=run_config)
                
                if result.success and result.screenshot:
                    data = base64.b64decode(result.screenshot)
                    fname = f"{i:03d}.png"
                    fpath = os.path.join(temp_folder, fname)
                    with open(fpath, "wb") as f:
                        f.write(data)
                    files_downloaded.append(fpath)
                else:
                    log_callback(f"[AVISO] Fallo al capturar página {i}")

        # Generate PDF
        if files_downloaded:
            files_downloaded.sort()
            safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip()
            pdf_name = f"{safe_title}.pdf"
            
            pdf_dir = os.path.join(current_dir, "output", PDF_FOLDER_NAME)
            os.makedirs(pdf_dir, exist_ok=True)
            output_pdf = os.path.join(pdf_dir, pdf_name)
            
            if create_pdf(files_downloaded, output_pdf, log_callback):
                 if os.path.exists(output_pdf): 
                     try: 
                         os.startfile(output_pdf) 
                     except: 
                         pass
        else:
            log_callback("[ERROR] No se capturaron imágenes.")
            
        if os.path.exists(temp_folder): 
            shutil.rmtree(temp_folder)
        log_callback("[HECHO] Finalizado.")
    
    async def _fetch_metadata(self, gallery_id: str, log_callback: Callable[[str], None]) -> tuple:
        """Fetch gallery metadata from Hitomi API."""
        total_images = 0
        title = f"Hitomi_{gallery_id}"
        try:
            async with aiohttp.ClientSession(headers=HEADERS_HITOMI) as session:
                async with session.get(f"https://ltn.gold-usergeneratedcontent.net/galleries/{gallery_id}.js") as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        text = text.replace("var galleryinfo =", "").strip()
                        if text.endswith(";"): 
                            text = text[:-1]
                        data = json.loads(text)
                        total_images = len(data.get('files', []))
                        if "title" in data: 
                            title = data["title"]
        except Exception as e:
            log_callback(f"[AVISO] Error obteniendo metadatos: {e}")
        
        if total_images == 0:
            log_callback(f"[AVISO] Metadatos no encontrados. Intentando {DEFAULT_PAGE_COUNT} páginas a ciegas.")
            total_images = DEFAULT_PAGE_COUNT
        else:
            log_callback(f"[INFO] Páginas Totales: {total_images}")
        
        return total_images, title
