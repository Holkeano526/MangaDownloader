import asyncio
import os
import shutil
import re
import json
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from typing import List, Optional, Callable

# External libraries
import aiohttp
from PIL import Image
from playwright.async_api import async_playwright

# Crawl4AI imports
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from crawl4ai.async_configs import LLMConfig
from crawl4ai.extraction_strategy import LLMExtractionStrategy

# ==============================================================================
# CONFIGURATION & CONSTANTS
# ==============================================================================

# API Key for Gemini (Required for TMO Site)
os.environ["GOOGLE_API_KEY"] = "AIzaSyBXzU2iIbOTWjiPsGyuXeT3aRwDampVps0"

# Browser Identity (User-Agent) to mimic a real user
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Site-Specific Headers
HEADERS_TMO = {"Referer": "https://tmohentai.com/", "User-Agent": USER_AGENT}
HEADERS_M440 = {"Referer": "https://m440.in/", "User-Agent": USER_AGENT}
HEADERS_H2R = {"Referer": "https://hentai2read.com/", "User-Agent": USER_AGENT}
HEADERS_HITOMI = {"Referer": "https://hitomi.la/", "User-Agent": USER_AGENT}

# Folder names
TEMP_FOLDER_NAME = "temp_manga_images"
PDF_FOLDER_NAME = "PDF"

# Constants
BATCH_SIZE = 10
DEFAULT_PAGE_COUNT = 60

# ==============================================================================
# SHARED UTILITIES
# ==============================================================================

async def download_image(session: aiohttp.ClientSession, url: str, folder: str, index: int, log_callback: Callable[[str], None], headers: dict) -> Optional[str]:
    """
    Downloads a single image from a URL and saves it to the specified folder.
    Returns the file path if successful, None otherwise.
    """
    try:
        # Determine file extension based on URL
        filename = f"{index:03d}.jpg" 
        if ".webp" in url: filename = f"{index:03d}.webp"
        elif ".png" in url: filename = f"{index:03d}.png"
        elif ".jpeg" in url: filename = f"{index:03d}.jpeg"
        elif ".avif" in url: filename = f"{index:03d}.avif"
        
        filepath = os.path.join(folder, filename)
        
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                content = await resp.read()
                with open(filepath, 'wb') as f:
                    f.write(content)
                return filepath
            else:
                log_callback(f"[ERROR] Fallo al descargar imagen {index}: Status {resp.status}")
                return None
    except Exception as e:
        log_callback(f"[ERROR] Fallo al descargar imagen {index}: {str(e)}")
        return None

def create_pdf(image_paths: List[str], output_pdf: str, log_callback: Callable[[str], None]) -> bool:
    """
    Combines a list of image paths into a single PDF file.
    Converts images to RGB mode (stripping alpha channel) to ensure compatibility.
    """
    if not image_paths:
        log_callback("[AVISO] No hay imágenes para compilar en el PDF.")
        return False

    images = []
    for path in image_paths:
        try:
            with Image.open(path) as img:
                # Convert to RGB to support saving as PDF (handles png transparency etc)
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                    images.append(img)
                else:
                    images.append(img.copy())
        except Exception as e:
            log_callback(f"[AVISO] Error leyendo imagen {path}: {e}")

    if images:
        try:
            # Save the first image and append the rest
            images[0].save(output_pdf, "PDF", resolution=100.0, save_all=True, append_images=images[1:])
            log_callback(f"[EXITO] PDF Generado: {os.path.basename(output_pdf)}")
            return True
        except Exception as e:
            log_callback(f"[ERROR] Fallo al guardar PDF: {e}")
            return False
    else:
        log_callback("[ERROR] No hay imágenes válidas para el PDF.")
        return False

def finalize_pdf_flow(image_paths: List[str], pdf_name: str, log_callback: Callable[[str], None], temp_dir: Optional[str] = None):
    """
    Shared Logic: Creates PDF, Opens it/Folder, and Cleans up temp dir.
    """
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Ensure PDF directory exists
    pdf_dir = os.path.join(current_dir, PDF_FOLDER_NAME)
    os.makedirs(pdf_dir, exist_ok=True)
    
    output_pdf = os.path.join(pdf_dir, pdf_name)
    log_callback(f"[INFO] Generando PDF: {pdf_name}")
    
    if create_pdf(image_paths, output_pdf, log_callback):
        # Try to open the file location for the user
        if os.path.exists(output_pdf):
            try: os.startfile(os.path.dirname(output_pdf))
            except: pass
            try: os.startfile(output_pdf)
            except: pass
        log_callback("[HECHO] Finalizado.")
    else:
        log_callback("[ERROR] No se pudo crear el PDF.")

    # Cleanup
    if temp_dir and os.path.exists(temp_dir):
        try:
            shutil.rmtree(temp_dir)
        except: pass

async def download_and_make_pdf(image_urls: List[str], output_name: str, headers: dict, log_callback: Callable[[str], None], check_cancel: Callable[[], bool], progress_callback: Optional[Callable[[int, int], None]] = None, is_path: bool = False) -> None:
    """
    Orchestration function: Downloads images in chunks -> Creates PDF/Folder -> Cleans up.
    """
    current_dir = os.path.dirname(os.path.abspath(__file__))
    temp_folder = os.path.join(current_dir, TEMP_FOLDER_NAME)
    
    # Clean/Create temp folder
    if os.path.exists(temp_folder): shutil.rmtree(temp_folder)
    os.makedirs(temp_folder, exist_ok=True)
    
    files = []
    
    # Download images using a single session
    async with aiohttp.ClientSession(headers=headers) as session:
        chunk_size = BATCH_SIZE 
        results = []
        for i in range(0, len(image_urls), chunk_size):
            if check_cancel():
                log_callback("[AVISO] Proceso cancelado por el usuario.")
                break
            chunk = image_urls[i:i+chunk_size]
            tasks = [download_image(session, u, temp_folder, i + idx + 1, log_callback, headers) for idx, u in enumerate(chunk)]
            res = await asyncio.gather(*tasks)
            results.extend(res)
            
            # Update Progress
            if progress_callback:
                progress_callback(min(i + chunk_size, len(image_urls)), len(image_urls))
            
        files = [f for f in results if f]
    
    files.sort()
    
    if files:
        if is_path:
            # Special case for M440 chapters where output_name is full path
            # We can't easily use the helper here without partial rewrite, or we just call create_pdf directly
            if create_pdf(files, output_name, log_callback):
                pass # Don't open every chapter if batch downloading
        else:
            # Use the cleaner helper
            finalize_pdf_flow(files, output_name, log_callback, temp_folder)
            return # Helper handles cleanup

    # Cleanup (if not handled by helper)
    if os.path.exists(temp_folder): shutil.rmtree(temp_folder)
    log_callback("[HECHO] Finalizado.")

# ==============================================================================
# SITE LOGIC: HITOMI.LA (Stealth Mode)
# ==============================================================================

async def process_hitomi(input_url: str, log_callback: Callable[[str], None], check_cancel: Callable[[], bool], progress_callback: Optional[Callable[[int, int], None]] = None) -> None:
    """
    Descarga imágenes de Hitomi usando Playwright para simular un usuario real
    y obtener imágenes de alta calidad (page-by-page).
    """
    id_match = re.search(r'[-/](\d+)\.html', input_url)
    if not id_match:
        log_callback("[ERROR] No se pudo extraer ID de la URL.")
        return
    gallery_id = int(id_match.group(1)) # Integer for logic
    
    log_callback(f"[INIT] Procesando Hitomi ID: {gallery_id} (Modo Navegador)...")
    
    # Create temp directory
    current_dir = os.path.dirname(os.path.abspath(__file__))
    temp_dir = os.path.join(current_dir, TEMP_FOLDER_NAME)
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
    os.makedirs(temp_dir)
    
    download_targets = []
    
    
    async with async_playwright() as p:
        # Launch visible browser to behave exactly like a user
        browser = await p.chromium.launch(headless=False, args=["--start-maximized"])
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={'width': 1280, 'height': 720}
        )
        page = await context.new_page()
        
        try:
            # 1. Inspect Metadata via API first (just to get count/title is faster)
            # We can still use the API for the "plan", but rely on browser for "execution"
            gallery_js_url = f"https://ltn.hitomi.la/galleries/{gallery_id}.js"
            title = f"Hitomi_{gallery_id}"
            
            # Navigate to Reader
            reader_url = f"https://hitomi.la/reader/{gallery_id}.html#1"
            log_callback(f"[INFO] Abriendo lector: {reader_url}")
            await page.goto(reader_url, wait_until="domcontentloaded")
            
            # Wait for title to load if possible (hitomi sets document title)
            await page.wait_for_timeout(2000)
            page_title = await page.title()
            if page_title:
                clean_title = re.sub(r'[\\/*?:"<>|]', '', page_title).strip()
                title = clean_title if clean_title else title
            log_callback(f"[INFO] Título detectado: {title}")

            # Get total images from galleryinfo
            # Hitomi usually defines 'galleryinfo' global variable
            total_images = await page.evaluate("() => window.galleryinfo ? window.galleryinfo.files.length : 0")
            
            if total_images == 0:
                log_callback("[INFO] 'galleryinfo' no detectado, intentando contar via API local...")
                # Fallback: fetch JS manually if browser didn't expose it globally yet
                # But usually reader loads it. Let's assume we can proceed page by page until failure.
                # Or wait for it.
                try:
                    await page.wait_for_function("() => window.galleryinfo && window.galleryinfo.files.length > 0", timeout=5000)
                    total_images = await page.evaluate("() => window.galleryinfo.files.length")
                except:
                    log_callback("[WARN] No se pudo determinar total de imágenes. Se intentará descubrir al vuelo.")
                    total_images = 9999 # Arbitrary limit

            log_callback(f"[INFO] Imágenes estimadas: {total_images}")

            # Loop through pages
            # Hitomi reader URL hash #1, #2, ... matches index+1
            
            for i in range(1, total_images + 1):
                if check_cancel():
                    log_callback("[AVISO] Proceso cancelado por el usuario.")
                    break

                try:
                    # Update hash to go to next image
                    # Hitomi checks hashchange event
                    current_url = f"https://hitomi.la/reader/{gallery_id}.html#{i}"
                    
                    # If we are already there (first page), just checking.
                    # Navigate via JS is faster/cleaner than page.goto for SPAs
                    await page.evaluate(f"location.hash = '#{i}'")
                    
                    # Wait for image to update. 
                    # Hitomi puts the main image in 'div.img-url img' or just 'img'
                    # We look for the image that matches the expected resolution or simply the VISIBLE one.
                    # A robust selector is often 'img' inside the main container.
                    selector = "div#comicImages img" 
                    
                    # Wait for the image to have a valid src (not empty)
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
                    
                    # Log what we found
                    log_callback(f"[DEBUG] Pág {i}: {img_src.split('/')[-1]} ({img_info['width']}x{img_info['height']})")
                    
                    # Download using Page Context with Explicit Referer
                    # Hitomi returns 404 if Referer is missing, even inside Playwright APIContext if not set
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
                        log_callback(f"[OK] Descargada {i}/{total_images}")
                        if progress_callback:
                            progress_callback(i, total_images)
                    else:
                        log_callback(f"[ERROR] Pág {i} falló con status {response.status}")
                    
                    # Small delay to be nice
                    await page.wait_for_timeout(500)
                    
                except Exception as e:
                    log_callback(f"[ERROR] Error en pág {i}: {e}")
                    # If failed heavily, break? Or continue?
                    # If total_images was guessed, maybe we reached end?
                    if total_images == 9999 and i > 5: # If we processed some but failed now
                        log_callback("[INFO] Posible fin de galería detectado.")
                        break

            log_callback(f"\n[INFO] Descarga finalizada. {len(download_targets)} imágenes obtenidas.")
            
        except Exception as e:
            log_callback(f"[ERROR] Error global Playwright: {e}")
            import traceback
            traceback.print_exc()
        finally:
            await browser.close()

    # Generate PDF via Shared Helper
    if download_targets:
        pdf_name = f"{re.sub(r'[\\/*?:"<>|]', '', title).strip()}.pdf"
        finalize_pdf_flow(download_targets, pdf_name, log_callback, temp_dir)
    else:
        log_callback("[ERROR] No se descargaron imágenes para crear el PDF.")

# ==============================================================================
# SITE LOGIC: TMOHentai / M440 / Hentai2Read
# ==============================================================================

async def process_tmo(input_url: str, log_callback: Callable[[str], None], check_cancel: Callable[[], bool], progress_callback: Optional[Callable[[int, int], None]] = None) -> None:
    """TMOHentai Logic (Uses Gemini AI for Extraction)."""
    log_callback("[INIT] Procesando TMO...")
    
    # Adjust URL for cascading view
    target_url = input_url
    if "/contents/" in input_url:
        target_url = input_url.replace("/contents/", "/reader/") + "/cascade"
    elif "/paginated/" in input_url:
        target_url = re.sub(r'/paginated/\d+', '/cascade', input_url)
    
    # Configure AI Extraction
    llm_config = LLMConfig(provider="gemini/gemini-1.5-flash", api_token=os.environ["GOOGLE_API_KEY"])
    instruction = "Estás en un lector de manga. Extrae TODAS las URLs de las imágenes. Busca 'data-original' y 'src'. Prioriza 'data-original'. Retorna JSON {'images': ['url1'...]}."
    llm_strategy = LLMExtractionStrategy(llm_config=llm_config, instruction=instruction)

    # JS to lazy-load images
    js_lazy_load = """
    (async () => {
        const sleep = (ms) => new Promise(r => setTimeout(r, ms));
        let totalHeight = 0; let distance = 500;
        while(totalHeight < document.body.scrollHeight) { window.scrollBy(0, distance); totalHeight += distance; await sleep(100); }
        window.scrollTo(0, 0);
        document.querySelectorAll('img[data-original]').forEach(img => { img.src = img.getAttribute('data-original'); });
        await sleep(1000);
    })();
    """

    async with AsyncWebCrawler(verbose=True) as crawler:
        result = await crawler.arun(target_url, extraction_strategy=llm_strategy, bypass_cache=True, js_code=js_lazy_load, wait_for="css:img.content-image")
        
        if result.success:
            image_urls = []
            # Parse AI Result
            try:
                if result.extracted_content:
                    clean = result.extracted_content
                    if "```json" in clean: clean = clean.split("```json")[1].split("```")[0].strip()
                    elif "```" in clean: clean = clean.split("```")[1].split("```")[0].strip()
                    image_urls = json.loads(clean).get("images", [])
            except Exception as e:
                log_callback(f"[AVISO] Error parseando IA: {e}")
            
            # Fallback Regex
            if not image_urls and result.html:
                matches = re.findall(r'data-original=["\'](https://[^"\']+\.(?:webp|jpg|png))["\']', result.html)
                if matches: image_urls = sorted(list(set(matches)))

            image_urls = [u for u in image_urls if "blank.gif" not in u]

            if image_urls:
                log_callback(f"[INFO] Imágenes encontradas: {len(image_urls)}")
                pdf_name = "manga_tmo.pdf"
                if result.html:
                    match = re.search(r'<h1[^>]*class=["\'].*?reader-title.*?["\'][^>]*>(.*?)</h1>', result.html, re.IGNORECASE | re.DOTALL)
                    if match:
                        safe = re.sub(r'[\\/*?:"<>|]', "", match.group(1).strip()).replace("\n", " ")
                        if safe: pdf_name = f"{safe}.pdf"
                
                await download_and_make_pdf(image_urls, pdf_name, HEADERS_TMO, log_callback, check_cancel, progress_callback)
            else:
                log_callback("[ERROR] No se encontraron imágenes vía IA o Regex.")
        else:
            log_callback(f"[ERROR] Crawler falló: {result.error_message}")

async def process_m440(input_url: str, log_callback: Callable[[str], None], check_cancel: Callable[[], bool], progress_callback: Optional[Callable[[int, int], None]] = None) -> None:
    """M440.in Logic."""
    log_callback("[INIT] Procesando M440...")

    async with AsyncWebCrawler(verbose=True) as crawler:
        result = await crawler.arun(url=input_url, bypass_cache=True)
        if not result.success:
            log_callback(f"[ERROR] Carga de página falló: {result.error_message}")
            return

        html = result.html
        html = result.html
        
        # Detect functionality based on URL structure
        # /manga/slug -> Cover Page
        # /manga/slug/chapter -> Single Chapter
        clean_url = input_url.split("?")[0].rstrip("/")
        is_cover_page = bool(re.search(r'/manga/[^/]+$', clean_url))
        
        # Backup check: Look for multiple chapter links if URL is ambiguous
        if not is_cover_page:
             potential_chapters = re.findall(r'href=["\'](https://m440.in/manga/[^/]+/[^"\']+)["\']', html)
             # If we find many chapter-like links (more than 3 to avoid next/prev buttons), assume cover
             if len(set(potential_chapters)) > 3:
                 is_cover_page = True

        if is_cover_page:
            log_callback("[INFO] Portada detectada. Extrayendo capítulos...")
            manga_title = "Manga_M440"
            title_match = re.search(r'<h2[^>]*class=["\']widget-title["\'][^>]*>(.*?)</h2>', html)
            if title_match:
                manga_title = re.sub(r'[\\/*?:"<>|]', "", title_match.group(1).strip())
            
            links = re.findall(r'href=["\'](https://m440.in/manga/[^/]+/[^"\']+)["\']', html)
            seen = set()
            clean_links = []
            for l in links:
                if l not in seen and "/manga/" in l and l != input_url:
                    seen.add(l)
                    clean_links.append(l)
            clean_links.reverse() 
            
            if not clean_links:
                log_callback("[ERROR] No se encontraron capítulos en la portada.")
                return

            current_dir = os.path.dirname(os.path.abspath(__file__))
            manga_dir = os.path.join(current_dir, PDF_FOLDER_NAME, manga_title)
            os.makedirs(manga_dir, exist_ok=True)

            for i, chap_url in enumerate(clean_links):
                if check_cancel():
                    log_callback("[AVISO] Proceso cancelado por el usuario.")
                    break
                
                if progress_callback: progress_callback(i + 1, len(clean_links))
                log_callback(f"Procesando Cap {i+1}/{len(clean_links)}")
                pdf_name = f"{manga_title} - {chap_url.split('/')[-1]}.pdf"
                full_pdf_path = os.path.join(manga_dir, pdf_name)
                if os.path.exists(full_pdf_path): continue
                # Pass None for progress because we track chapters here, not internal images
                await process_m440_chapter(chap_url, full_pdf_path, crawler, log_callback, check_cancel, None)
            
            os.startfile(manga_dir)
        else:
            # Single Chapter
            log_callback("[INFO] Capítulo individual detectado.")
            pdf_name = "m440_chapter.pdf"
            current_dir = os.path.dirname(os.path.abspath(__file__))
            pdf_dir = os.path.join(current_dir, PDF_FOLDER_NAME)
            os.makedirs(pdf_dir, exist_ok=True)
            
            full_pdf_path = os.path.join(pdf_dir, pdf_name)
            await process_m440_chapter(input_url, full_pdf_path, crawler, log_callback, check_cancel, progress_callback)
            if os.path.exists(full_pdf_path): 
                try: os.startfile(os.path.dirname(full_pdf_path))
                except: pass
                
                try: os.startfile(full_pdf_path)
                except: pass

async def process_m440_chapter(url: str, output_pdf_path: str, crawler: AsyncWebCrawler, log_callback: Callable[[str], None], check_cancel: Callable[[], bool], progress_callback: Optional[Callable[[int, int], None]] = None) -> None:
    result = await crawler.arun(url=url, bypass_cache=True)
    if not result.success: return
    html = result.html
    matches = re.findall(r'data-src=["\'](https://[^"\']+)["\']', html)
    if matches:
        images = list(dict.fromkeys(matches))
        log_callback(f"[INFO] Descargando {len(images)} imágenes...")
        await download_and_make_pdf(images, output_pdf_path, HEADERS_M440, log_callback, check_cancel, progress_callback, is_path=True)

async def process_h2r(input_url: str, log_callback: Callable[[str], None], check_cancel: Callable[[], bool], progress_callback: Optional[Callable[[int, int], None]] = None) -> None:
    """Hentai2Read Logic."""
    log_callback("[INIT] Procesando Hentai2Read...")
    
    async with AsyncWebCrawler(verbose=True) as crawler:
        result = await crawler.arun(url=input_url, bypass_cache=True)
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
                    cdn_match = re.search(r'src=["\'](https://[^"/]+/hentai)/', html)
                    if cdn_match: base_url = cdn_match.group(1)
                    
                    image_urls = [f"{base_url}{p}" if not p.startswith("http") else p for p in paths]
                    log_callback(f"[INFO] Se encontraron {len(image_urls)} imágenes.")
                    
                    pdf_name = "hentai2read_chapter.pdf"
                    title_match = re.search(r'[\'"]title[\'"]\s*:\s*[\'"](.*?)[\'"]', json_str)
                    if title_match:
                        safe = re.sub(r'[\\/*?:"<>|]', "", title_match.group(1).strip())
                        pdf_name = f"{safe}.pdf"
                    
                    await download_and_make_pdf(image_urls, pdf_name, HEADERS_H2R, log_callback, check_cancel, progress_callback)
                else:
                    log_callback("[ERROR] No se pudo extraer la lista de imágenes.")
            except Exception as e:
                log_callback(f"[ERROR] Error procesando metadatos: {e}")
        else:
            log_callback("[ERROR] No se encontraron datos del capítulo.")

async def process_nhentai(input_url: str, log_callback: Callable[[str], None], check_cancel: Callable[[], bool], progress_callback: Optional[Callable[[int, int], None]] = None) -> None:
    """nhentai.net Logic using Playwright API Access."""
    id_match = re.search(r'nhentai\.net/g/(\d+)', input_url)
    if not id_match:
        log_callback("[ERROR] No se pudo extraer ID de la URL.")
        return
    gallery_id = id_match.group(1)
    
    log_callback(f"[INIT] Procesando nhentai ID: {gallery_id}...")
    
    api_url = f"https://nhentai.net/api/gallery/{gallery_id}"
    
    current_dir = os.path.dirname(os.path.abspath(__file__))
    temp_dir = os.path.join(current_dir, TEMP_FOLDER_NAME)
    if os.path.exists(temp_dir): shutil.rmtree(temp_dir)
    os.makedirs(temp_dir)

    images_data = []
    title = f"nhentai_{gallery_id}"
    media_id = ""

    # Fetch Metadata via Playwright (bypassing generic restrictions)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False) # Visible to pass checks
        page = await browser.new_page()
        try:
            log_callback("[INFO] Obteniendo metadatos...")
            await page.goto(api_url, wait_until="domcontentloaded")
            
            # Browser might wrap JSON in PRE tag
            content = await page.inner_text("body")
            
            try:
                data = json.loads(content)
                if "title" in data:
                    title = data["title"].get("pretty", data["title"].get("english", title))
                
                media_id = data.get("media_id")
                images_list = data.get("images", {}).get("pages", [])
                
                # Extension map
                ext_map = {'j': 'jpg', 'p': 'png', 'w': 'webp'}
                
                for idx, img in enumerate(images_list):
                    t = img.get('t')
                    ext = ext_map.get(t, 'jpg')
                    # Format: https://i.nhentai.net/galleries/{media_id}/{page_num}.{ext}
                    img_url = f"https://i.nhentai.net/galleries/{media_id}/{idx+1}.{ext}"
                    images_data.append(img_url)
                    
            except json.JSONDecodeError:
                log_callback("[ERROR] Fallo al parsear respuesta API.")
                return
                
        except Exception as e:
            log_callback(f"[ERROR] Error obteniendo metadatos: {e}")
            return
        finally:
            await browser.close()
            
    if images_data:
        log_callback(f"[INFO] Galería: {title} ({len(images_data)} imgs)")
        
        # Download images logic
        # nhentai usually doesn't need Referer for 'i.nhentai.net', but good to have
        headers = {"User-Agent": USER_AGENT} 
        
        # Re-use shared finalizer? 
        # But we need to download first. 
        # We can reuse download_and_make_pdf logic but simpler since we have URLs.
        
        pdf_name = f"{re.sub(r'[\\/*?:"<>|]', '', title).strip()}.pdf"
        await download_and_make_pdf(images_data, pdf_name, headers, log_callback, check_cancel, progress_callback)
    else:
        log_callback("[ERROR] No se encontraron imágenes.")

async def process_entry(url: str, log_callback: Callable[[str], None], check_cancel: Callable[[], bool], progress_callback: Optional[Callable[[int, int], None]] = None):
    """Main Router: Redirects to specific site handler based on URL."""
    if "tmohentai" in url:
        await process_tmo(url, log_callback, check_cancel, progress_callback=progress_callback)
    elif "m440.in" in url or "mangas.in" in url:
        await process_m440(url, log_callback, check_cancel, progress_callback=progress_callback)
    elif "hentai2read" in url:
        await process_h2r(url, log_callback, check_cancel, progress_callback=progress_callback)
    elif "hitomi.la" in url:
        await process_hitomi(url, log_callback, check_cancel, progress_callback=progress_callback)
    elif "nhentai.net" in url:
        await process_nhentai(url, log_callback, check_cancel, progress_callback=progress_callback)
    else:
        log_callback("[ERROR] Sitio web no soportado.")

# ==============================================================================
# GUI APPLICATION
# ==============================================================================

class DownloaderApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cancelled = False
        self.root.title("Universal Manga Downloader")
        self.root.geometry("800x600")
        
        # Styles
        style = ttk.Style()
        style.configure("TButton", font=("Segoe UI", 10))
        style.configure("TLabel", font=("Segoe UI", 11))
        
        # Main Layout
        main_frame = ttk.Frame(root, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Header
        ttk.Label(main_frame, text="Manga PDF Downloader", font=("Segoe UI", 16, "bold")).pack(pady=(0, 20))
        
        # Input Area
        input_frame = ttk.LabelFrame(main_frame, text="Input", padding="10")
        input_frame.pack(fill=tk.X, pady=(0, 15))
        
        ttk.Label(input_frame, text="URL (TMO, M440, H2R, Hitomi, nhentai):").pack(anchor=tk.W)
        self.url_entry = ttk.Entry(input_frame)
        self.url_entry.pack(fill=tk.X, pady=(5, 10))
        
        self.placeholder_text = "Pega tu URL aquí..."
        self.url_entry.insert(0, self.placeholder_text)
        self.url_entry.config(foreground='grey')

        self.url_entry.bind("<FocusIn>", self._on_entry_focus_in)
        self.url_entry.bind("<FocusOut>", self._on_entry_focus_out)
        
        self.btn_start = ttk.Button(input_frame, text="Descargar PDF", command=self.start_process)
        self.btn_start.pack(fill=tk.X, pady=(0, 5))
        
        self.btn_cancel = ttk.Button(input_frame, text="Cancelar Detener", command=self.cancel_process, state='disabled')
        self.btn_cancel.pack(fill=tk.X, pady=(0, 5))

        # Progress Bar
        self.progress = ttk.Progressbar(input_frame, orient="horizontal", length=100, mode="determinate")
        self.progress.pack(fill=tk.X, pady=(0, 10))
        
        # Logging Area
        log_frame = ttk.LabelFrame(main_frame, text="Logs", padding="10")
        log_frame.pack(fill=tk.BOTH, expand=True)
        
        self.log_area = scrolledtext.ScrolledText(log_frame, state='disabled', font=("Consolas", 9))
        self.log_area.pack(fill=tk.BOTH, expand=True)

    def _on_entry_focus_in(self, event) -> None:
        if self.url_entry.get() == self.placeholder_text:
            self.url_entry.delete(0, tk.END)
            self.url_entry.config(foreground='black')

    def _on_entry_focus_out(self, event) -> None:
        if not self.url_entry.get():
            self.url_entry.insert(0, self.placeholder_text)
            self.url_entry.config(foreground='grey')

    def log(self, message: str) -> None:
        """Appends message to GUI log and File log."""
        self.log_area.config(state='normal')
        self.log_area.insert(tk.END, message + "\n")
        self.log_area.see(tk.END)
        self.log_area.config(state='disabled')
        
        # File Logging
        try:
            with open("downloader_debug.log", "a", encoding="utf-8") as f:
                f.write(message + "\n")
        except: pass

    def start_process(self) -> None:
        self.cancelled = False
        url = self.url_entry.get().strip()
        if not url or url == self.placeholder_text:
            messagebox.showwarning("Aviso", "Por favor ingrese una URL.")
            return

        supported_domains = ["tmohentai", "m440.in", "mangas.in", "hentai2read", "hitomi.la", "nhentai.net"]
        if not any(domain in url for domain in supported_domains):
             messagebox.showwarning("Aviso", "URL no soportada.\nDominios válidos: tmohentai, m440.in, hentai2read, hitomi.la, nhentai.net")
             return
        
        # Init Log
        try:
            with open("downloader_debug.log", "w", encoding="utf-8") as f:
                f.write("=== LOG START ===\n")
        except Exception as e:
            print(f"Error escribiendo log: {e}")
        
        self.progress['value'] = 0
        self.btn_start.config(state='disabled')
        self.btn_cancel.config(state='normal')
        self.log_area.config(state='normal')
        self.log_area.delete(1.0, tk.END)
        self.log_area.config(state='disabled')
        
        # Run in separate thread to prevent GUI freeze
        threading.Thread(target=self.run_async, args=(url,), daemon=True).start()

    def cancel_process(self) -> None:
        """Establece la bandera de cancelación para detener las tareas asíncronas en curso."""
        self.cancelled = True
        self.log("[AVISO] Solicitando cancelación...")
        self.btn_cancel.config(state='disabled')

    def run_async(self, url: str) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Thread-safe log adapter
        def safe_log(msg): self.root.after(0, self.log, msg)
        check_cancel = lambda: self.cancelled

        # Thread-safe progress adapter
        def safe_progress(current, total):
            def _update():
                self.progress['maximum'] = total
                self.progress['value'] = current
                # self.root.update_idletasks() # Optional, might cause flickering if too fast
            self.root.after(0, _update)

        try:
            loop.run_until_complete(process_entry(url, safe_log, check_cancel, progress_callback=safe_progress))
        finally:
            loop.close()
            self.root.after(0, lambda: self.reset_buttons())
            
    def reset_buttons(self):
        self.btn_start.config(state='normal')
        self.btn_cancel.config(state='disabled')

if __name__ == "__main__":
    root = tk.Tk()
    app = DownloaderApp(root)
    root.mainloop()