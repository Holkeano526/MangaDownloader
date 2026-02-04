import asyncio
import os
import shutil
import re
import json
from typing import List, Optional, Callable
import aiohttp
from PIL import Image
from playwright.async_api import async_playwright
from crawl4ai import AsyncWebCrawler
from crawl4ai.async_configs import LLMConfig
from crawl4ai.extraction_strategy import LLMExtractionStrategy
from dotenv import load_dotenv

# ==============================================================================
# CONFIGURACIÓN & CONSTANTES
# ==============================================================================

# Cargar variables de entorno
load_dotenv()

# API Key Check
if not os.getenv("GOOGLE_API_KEY"):
    print("[WARN] GOOGLE_API_KEY no encontrada en .env")

# Browser Identity
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Site-Specific Headers
HEADERS_TMO = {"Referer": "https://tmohentai.com/", "User-Agent": USER_AGENT}
HEADERS_M440 = {"Referer": "https://m440.in/", "User-Agent": USER_AGENT}
HEADERS_H2R = {"Referer": "https://hentai2read.com/", "User-Agent": USER_AGENT}
HEADERS_HITOMI = {"Referer": "https://hitomi.la/", "User-Agent": USER_AGENT}
HEADERS_ZONATMO = {"Referer": "https://zonatmo.com/", "User-Agent": USER_AGENT}

# Folder names
TEMP_FOLDER_NAME = "temp_manga_images"
PDF_FOLDER_NAME = "PDF"

# Constants
BATCH_SIZE = 10
DEFAULT_PAGE_COUNT = 60

# CONFIGURACIÓN GLOBAL
OPEN_RESULT_ON_FINISH = True  # El bot cambiará esto a False

# ==============================================================================
# SHARED UTILITIES
# ==============================================================================

def clean_filename(text: str) -> str:
    """Elimina caracteres inválidos para nombres de archivo en Windows/Linux."""
    if not text: return "untitled"
    # Eliminar tags HTML si quedaron
    text = re.sub(r'<[^>]+>', '', text)
    # Eliminar caracteres prohibidos
    safe = re.sub(r'[\\/*?:"<>|]', "", text).strip()
    return safe if safe else "untitled"

async def download_image(session: aiohttp.ClientSession, url: str, folder: str, index: int, log_callback: Callable[[str], None], headers: dict) -> Optional[str]:
    """Descarga una sola imagen y retorna su ruta local."""
    try:
        # Determinar extensión (default jpg)
        ext = ".jpg"
        if ".webp" in url: ext = ".webp"
        elif ".png" in url: ext = ".png"
        elif ".jpeg" in url: ext = ".jpeg"
        elif ".avif" in url: ext = ".avif"
        
        filename = f"{index:03d}{ext}"
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

import img2pdf

def create_pdf(image_paths: List[str], output_pdf: str, log_callback: Callable[[str], None]) -> bool:
    """Compila una lista de rutas de imágenes en un único PDF usadno img2pdf (Zero-RAM overhead)."""
    if not image_paths:
        log_callback("[AVISO] No hay imágenes para compilar en el PDF.")
        return False

    try:
        # img2pdf es mucho más eficiente porque incrusta los bytes JPG directos sin re-codificar.
        with open(output_pdf, "wb") as f:
            f.write(img2pdf.convert(image_paths))
            
        log_callback(f"[EXITO] PDF Generado: {os.path.basename(output_pdf)}")
        return True
    except Exception as e:
        log_callback(f"[ERROR] Fallo al guardar PDF (img2pdf): {e}")
        # Fallback a método antiguo si img2pdf falla (ej por formatos raros)
        try:
            log_callback("[INFO] Intentando método alternativo (Pillow)...")
            images = []
            for path in image_paths:
                with Image.open(path) as img:
                    if img.mode in ("RGBA", "P"): img = img.convert("RGB")
                    images.append(img.copy()) # Copy to ensure file is not closed prematurely if using context manager weirdly
            if images:
                images[0].save(output_pdf, "PDF", resolution=100.0, save_all=True, append_images=images[1:])
                return True
        except Exception as e2:
             log_callback(f"[ERROR] Falló método alternativo: {e2}")
        
        return False

def finalize_pdf_flow(image_paths: List[str], pdf_name: str, log_callback: Callable[[str], None], temp_dir: Optional[str] = None):
    current_dir = os.path.dirname(os.path.abspath(__file__))
    pdf_dir = os.path.join(current_dir, PDF_FOLDER_NAME)
    os.makedirs(pdf_dir, exist_ok=True)
    
    output_pdf = os.path.join(pdf_dir, pdf_name)
    log_callback(f"[INFO] Generando PDF: {pdf_name}")
    
    if create_pdf(image_paths, output_pdf, log_callback):
        # SOLO ABRIR SI ESTÁ HABILITADO
        if OPEN_RESULT_ON_FINISH:
            if os.path.exists(output_pdf):
                try: os.startfile(os.path.dirname(output_pdf))
                except: pass
                try: os.startfile(output_pdf)
                except: pass
        log_callback("[HECHO] Finalizado.")
    else:
        log_callback("[ERROR] No se pudo crear el PDF.")

    if temp_dir and os.path.exists(temp_dir):
        try: shutil.rmtree(temp_dir)
        except: pass

async def download_and_make_pdf(image_urls: List[str], output_name: str, headers: dict, log_callback: Callable[[str], None], check_cancel: Callable[[], bool], progress_callback: Optional[Callable[[int, int], None]] = None, is_path: bool = False) -> None:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    temp_folder = os.path.join(current_dir, TEMP_FOLDER_NAME)
    
    if os.path.exists(temp_folder): shutil.rmtree(temp_folder)
    os.makedirs(temp_folder, exist_ok=True)
    
    files = []
    
    async with aiohttp.ClientSession(headers=headers) as session:
        chunk_size = BATCH_SIZE 
        results = []
        for i in range(0, len(image_urls), chunk_size):
            if check_cancel and check_cancel():
                log_callback("[AVISO] Proceso cancelado por el usuario.")
                break
            chunk = image_urls[i:i+chunk_size]
            tasks = [download_image(session, u, temp_folder, i + idx + 1, log_callback, headers) for idx, u in enumerate(chunk)]
            res = await asyncio.gather(*tasks)
            results.extend(res)
            
            if progress_callback:
                progress_callback(min(i + chunk_size, len(image_urls)), len(image_urls))
            
        files = [f for f in results if f]
    
    files.sort()
    
    if files:
        if is_path:
            if create_pdf(files, output_name, log_callback):
                pass
        else:
            finalize_pdf_flow(files, output_name, log_callback, temp_folder)
            return

    if os.path.exists(temp_folder): shutil.rmtree(temp_folder)
    
    if not is_path:
        log_callback("[HECHO] Finalizado.")
    else:
        # In bulk mode, individual completion is implicit or logged by caller
        pass

# ==============================================================================
# SITE LOGIC
# ==============================================================================

async def process_hitomi(input_url: str, log_callback: Callable[[str], None], check_cancel: Callable[[], bool], progress_callback: Optional[Callable[[int, int], None]] = None) -> None:
    id_match = re.search(r'[-/](\d+)\.html', input_url)
    if not id_match:
        log_callback("[ERROR] No se pudo extraer ID de la URL.")
        return
    gallery_id = int(id_match.group(1))
    
    log_callback(f"[INIT] Procesando Hitomi ID: {gallery_id} (Modo Navegador)...")
    
    current_dir = os.path.dirname(os.path.abspath(__file__))
    temp_dir = os.path.join(current_dir, TEMP_FOLDER_NAME)
    if os.path.exists(temp_dir): shutil.rmtree(temp_dir)
    os.makedirs(temp_dir)
    
    download_targets = []
    
    async with async_playwright() as p:
        # HEADLESS TRUE SI ES BOT PODRÍA SER MEJOR, PERO HITOMI REQUIERE VISUAL A VECES.
        # Open in headless=False usually works best for stealth, doing True if needed can be configured.
        browser = await p.chromium.launch(headless=False, args=["--start-maximized"])
        context = await browser.new_context(user_agent=USER_AGENT, viewport={'width': 1280, 'height': 720})
        page = await context.new_page()
        
        try:
            reader_url = f"https://hitomi.la/reader/{gallery_id}.html#1"
            log_callback(f"[INFO] Abriendo lector: {reader_url}")
            await page.goto(reader_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)
            
            title = f"Hitomi_{gallery_id}"
            page_title = await page.title()
            if page_title:
                clean_title = re.sub(r'[\\/*?:"<>|]', '', page_title).strip()
                title = clean_title if clean_title else title
            log_callback(f"[INFO] Título detectado: {title}")

            total_images = await page.evaluate("() => window.galleryinfo ? window.galleryinfo.files.length : 0")
            if total_images == 0:
                try:
                    await page.wait_for_function("() => window.galleryinfo && window.galleryinfo.files.length > 0", timeout=5000)
                    total_images = await page.evaluate("() => window.galleryinfo.files.length")
                except:
                    total_images = 9999

            log_callback(f"[INFO] Imágenes estimadas: {total_images}")

            for i in range(1, total_images + 1):
                if check_cancel and check_cancel(): break

                try:
                    await page.evaluate(f"location.hash = '#{i}'")
                    selector = "div#comicImages img" 
                    await page.wait_for_function("""(selector) => { const img = document.querySelector(selector); return img && img.src && img.src.indexOf('http') === 0; }""", arg=selector, timeout=10000)
                    
                    img_info = await page.evaluate("""(selector) => { const img = document.querySelector(selector); return {src: img.src, width: img.naturalWidth, height: img.naturalHeight}; }""", selector)
                    img_src = img_info['src']
                    
                    log_callback(f"[DEBUG] Pág {i}: {img_src.split('/')[-1]} ({img_info['width']}x{img_info['height']})")
                    
                    headers = {"Referer": f"https://hitomi.la/reader/{gallery_id}.html"}
                    response = await page.request.get(img_src, headers=headers)
                    
                    if response.status == 200:
                        data = await response.body()
                        ext = img_src.split('.')[-1].split('?')[0]
                        filename = f"{i:03d}.{ext}"
                        filepath = os.path.join(temp_dir, filename)
                        with open(filepath, 'wb') as f: f.write(data)
                        download_targets.append(filepath)
                        log_callback(f"[OK] Descargada {i}/{total_images}")
                        if progress_callback: progress_callback(i, total_images)
                    else:
                        log_callback(f"[ERROR] Pág {i} falló con status {response.status}")
                    
                    await page.wait_for_timeout(500)
                    
                except Exception as e:
                    log_callback(f"[ERROR] Error en pág {i}: {e}")
                    if total_images == 9999 and i > 5: break
            
            log_callback(f"\n[INFO] Descarga finalizada. {len(download_targets)} imágenes obtenidas.")
            
        except Exception as e:
            log_callback(f"[ERROR] Error global Playwright: {e}")
        finally:
            await browser.close()

    if download_targets:
        pdf_name = f"{clean_filename(title)}.pdf"
        finalize_pdf_flow(download_targets, pdf_name, log_callback, temp_dir)
    else:
        log_callback("[ERROR] No se descargaron imágenes para crear el PDF.")

async def process_tmo(input_url: str, log_callback: Callable[[str], None], check_cancel: Callable[[], bool], progress_callback: Optional[Callable[[int, int], None]] = None) -> None:
    log_callback("[INIT] Procesando TMO...")
    
    target_url = input_url
    if "/contents/" in input_url:
        target_url = input_url.replace("/contents/", "/reader/") + "/cascade"
    elif "/paginated/" in input_url:
        target_url = re.sub(r'/paginated/\d+', '/cascade', input_url)
    
    llm_config = LLMConfig(provider="gemini/gemini-1.5-flash", api_token=os.environ["GOOGLE_API_KEY"])
    instruction = "Estás en un lector de manga. Extrae TODAS las URLs de las imágenes. Busca 'data-original' y 'src'. Prioriza 'data-original'. Retorna JSON {'images': ['url1'...]}."
    llm_strategy = LLMExtractionStrategy(llm_config=llm_config, instruction=instruction)

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
            try:
                if result.extracted_content:
                    clean = result.extracted_content
                    if "```json" in clean: clean = clean.split("```json")[1].split("```")[0].strip()
                    elif "```" in clean: clean = clean.split("```")[1].split("```")[0].strip()
                    image_urls = json.loads(clean).get("images", [])
            except Exception as e:
                log_callback(f"[AVISO] Error parseando IA: {e}")
            
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
                        safe = clean_filename(match.group(1).strip()).replace("\n", " ")
                        if safe: pdf_name = f"{safe}.pdf"
                
                await download_and_make_pdf(image_urls, pdf_name, HEADERS_TMO, log_callback, check_cancel, progress_callback)
            else:
                log_callback("[ERROR] No se encontraron imágenes vía IA o Regex.")
        else:
            log_callback(f"[ERROR] Crawler falló: {result.error_message}")

async def process_m440(input_url: str, log_callback: Callable[[str], None], check_cancel: Callable[[], bool], progress_callback: Optional[Callable[[int, int], None]] = None) -> None:
    log_callback("[INIT] Procesando M440...")

    async with AsyncWebCrawler(verbose=True) as crawler:
        result = await crawler.arun(url=input_url, bypass_cache=True)
        if not result.success:
            log_callback(f"[ERROR] Carga de página falló: {result.error_message}")
            return

        html = result.html
        clean_url = input_url.split("?")[0].rstrip("/")
        is_cover_page = bool(re.search(r'/manga/[^/]+$', clean_url))
        
        if not is_cover_page:
             potential_chapters = re.findall(r'href=["\'](https://m440.in/manga/[^/]+/[^"\']+)["\']', html)
             if len(set(potential_chapters)) > 3: is_cover_page = True

        if is_cover_page:
            log_callback("[INFO] Portada detectada. Extrayendo capítulos...")
            manga_title = "Manga_M440"
            title_match = re.search(r'<h2[^>]*class=["\']widget-title["\'][^>]*>(.*?)</h2>', html)
            if title_match: manga_title = clean_filename(title_match.group(1).strip())
            
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
                if check_cancel and check_cancel(): break
                if progress_callback: progress_callback(i + 1, len(clean_links))
                log_callback(f"Procesando Cap {i+1}/{len(clean_links)}")
                pdf_name = f"{manga_title} - {chap_url.split('/')[-1]}.pdf"
                full_pdf_path = os.path.join(manga_dir, pdf_name)
                if os.path.exists(full_pdf_path): continue
                await process_m440_chapter(chap_url, full_pdf_path, crawler, log_callback, check_cancel, None)
            
            if OPEN_RESULT_ON_FINISH:
                try: os.startfile(manga_dir)
                except: pass
        else:
            log_callback("[INFO] Capítulo individual detectado.")
            pdf_name = "m440_chapter.pdf"
            current_dir = os.path.dirname(os.path.abspath(__file__))
            pdf_dir = os.path.join(current_dir, PDF_FOLDER_NAME)
            os.makedirs(pdf_dir, exist_ok=True)
            full_pdf_path = os.path.join(pdf_dir, pdf_name)
            await process_m440_chapter(input_url, full_pdf_path, crawler, log_callback, check_cancel, progress_callback)
            if OPEN_RESULT_ON_FINISH and os.path.exists(full_pdf_path): 
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
    log_callback("[INIT] Procesando Hentai2Read...")
    async with AsyncWebCrawler(verbose=True) as crawler:
        result = await crawler.arun(url=input_url, bypass_cache=True)
        if not result.success:
            log_callback(f"[ERROR] Page load failed: {result.error_message}")
            return
            
        html = result.html
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
                        safe = clean_filename(title_match.group(1).strip())
                        pdf_name = f"{safe}.pdf"
                    
                    await download_and_make_pdf(image_urls, pdf_name, HEADERS_H2R, log_callback, check_cancel, progress_callback)
                else:
                    log_callback("[ERROR] No se pudo extraer la lista de imágenes.")
            except Exception as e:
                log_callback(f"[ERROR] Error procesando metadatos: {e}")
        else:
            log_callback("[ERROR] No se encontraron datos del capítulo.")

async def process_nhentai(input_url: str, log_callback: Callable[[str], None], check_cancel: Callable[[], bool], progress_callback: Optional[Callable[[int, int], None]] = None) -> None:
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

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        try:
            log_callback("[INFO] Obteniendo metadatos...")
            await page.goto(api_url, wait_until="domcontentloaded")
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
        headers = {"User-Agent": USER_AGENT} 
        pdf_name = f"{clean_filename(title)}.pdf"
        await download_and_make_pdf(images_data, pdf_name, headers, log_callback, check_cancel, progress_callback)
    else:
        log_callback("[ERROR] No se encontraron imágenes.")



async def process_zonatmo(input_url: str, log_callback: Callable[[str], None], check_cancel: Callable[[], bool], progress_callback: Optional[Callable[[int, int], None]] = None) -> None:
    """Proceso principal para ZonaTMO (Portadas y Capítulos)."""
    log_callback("[INIT] Procesando ZonaTMO...")
    
    # 1. Modo Portada (Lista de capítulos)
    if "/library/manga/" in input_url:
        log_callback("[INFO] Portada detectada. Buscando capítulos...")
        async with AsyncWebCrawler(verbose=True) as crawler:
            result = await crawler.arun(url=input_url, bypass_cache=True)
            if not result.success:
                log_callback(f"[ERROR] Fallo al cargar portada: {result.error_message}")
                return
            
            # Extraer links de capítulos
            links = re.findall(r'href=["\'](https://zonatmo.com/view_uploads/[^"\']+)["\']', result.html)
            
            # Limpiar duplicados manteniendo orden
            clean_links = []
            seen = set()
            for l in links:
                if l not in seen:
                    clean_links.append(l)
                    seen.add(l)
            
            if not clean_links:
                log_callback("[ERROR] No se encontraron capítulos.")
                return

            log_callback(f"[INFO] Se encontraron {len(clean_links)} capítulos.")
            
            manga_title = "Manga_ZonaTMO"
            
            # Intento 1: H1 Específico de ZonaTMO
            # <h1 class="element-title my-2"> Título <small>(Año)</small> </h1>
            h1_match = re.search(r'<h1[^>]*class=["\'].*?element-title.*?["\'][^>]*>(.*?)</h1>', result.html, re.IGNORECASE | re.DOTALL)
            
            if h1_match: 
                raw_html = h1_match.group(1)
                # Quitar tag year
                raw_html = re.sub(r'<small[^>]*>.*?</small>', '', raw_html, flags=re.IGNORECASE | re.DOTALL)
                raw_html = re.sub(r'\s+', ' ', raw_html) # espacios extra
                candidate = clean_filename(raw_html) 
                if candidate and candidate != "untitled":
                     manga_title = candidate
            
            # Intento 2: Title Tag
            if manga_title == "Manga_ZonaTMO":
                title_tag = re.search(r'<title>(.*?)</title>', result.html, re.IGNORECASE)
                if title_tag:
                    raw = title_tag.group(1).split('|')[0].split('-')[0].strip()
                    manga_title = clean_filename(raw)

            if not manga_title or len(manga_title) < 2: manga_title = "Manga_ZonaTMO"
            
            log_callback(f"[INFO] Título detectado: {manga_title}")
            
            current_dir = os.path.dirname(os.path.abspath(__file__))
            manga_dir = os.path.join(current_dir, PDF_FOLDER_NAME, manga_title)
            os.makedirs(manga_dir, exist_ok=True)
            
            # Descargar en orden inverso (Cap 1 primero) si la web los lista new->old
            clean_links.reverse()

            for i, chap_url in enumerate(clean_links):
                if check_cancel and check_cancel(): break
                if progress_callback: progress_callback(i + 1, len(clean_links))
                
                # Naming: MangaName - 001.pdf
                pdf_name = f"{manga_title} - {i+1:03d}.pdf"
                full_pdf_path = os.path.join(manga_dir, pdf_name)
                
                if os.path.exists(full_pdf_path): 
                    continue
                
                log_callback(f"Procesando Cap {i+1}/{len(clean_links)}")
                
                try:
                    await process_zonatmo_chapter(chap_url, full_pdf_path, log_callback, check_cancel, None)
                    await asyncio.sleep(1) # Rate limit friendly
                except Exception as e:
                    log_callback(f"[ERROR] Falló capítulo {i+1}: {e}")
            
            if OPEN_RESULT_ON_FINISH:
                try: os.startfile(manga_dir)
                except: pass

    # 2. Modo Capítulo Único
    else:
        pdf_name = "zonatmo_chapter.pdf"
        await process_zonatmo_chapter(input_url, pdf_name, log_callback, check_cancel, progress_callback)

async def process_zonatmo_chapter(url: str, output_name: str, log_callback: Callable[[str], None], check_cancel: Callable[[], bool], progress_callback: Optional[Callable[[int, int], None]] = None) -> None:
    """Procesa un capítulo individual de ZonaTMO: Redirección -> Cascade -> Imágenes."""
    target_url = url
    
    # Resolver redirecciones (view_uploads -> viewer/.../paginated)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=HEADERS_ZONATMO) as resp:
                if resp.status == 200:
                    final_url = str(resp.url)
                    if "/paginated" in final_url:
                        target_url = final_url.replace("/paginated", "/cascade")
                    elif "/viewer/" in final_url:
                         if not final_url.endswith("/cascade"):
                             target_url = final_url + "/cascade"
                else:
                    log_callback(f"[AVISO] Fallo al resolver URL: {resp.status}, usando original.")
    except Exception as e:
         log_callback(f"[DEBUG] Error resolviendo redirección: {e}")

    log_callback(f"[INFO] URL Cascada: {target_url}")

    # Configuración LLM (para casos difíciles)
    # Nota: Realmente ZonaTMO parece funcionar mejor con Regex puro en muchos casos,
    # pero mantenemos el Crawler+LLM como fallback robusto si se desea.
    # Por ahora, usaremos las regex directas sobre el result.html que devuelve Crawler
    # ya que es más rápido/barato si funciona.
    
    llm_config = LLMConfig(provider="gemini/gemini-1.5-flash", api_token=os.environ["GOOGLE_API_KEY"])
    instruction = "Estás en un lector de manga. Extrae TODAS las URLs de las imágenes. Busca 'data-original' y 'src'. Retorna JSON {'images': ['url1'...]}."
    llm_strategy = LLMExtractionStrategy(llm_config=llm_config, instruction=instruction)
    
    js_lazy_load = """
    (async () => {
        const sleep = (ms) => new Promise(r => setTimeout(r, ms));
        window.scrollTo(0, 0);
        let totalHeight = 0; let distance = 1000;
        while(totalHeight < document.body.scrollHeight) { window.scrollBy(0, distance); totalHeight += distance; await sleep(200); }
        await sleep(1000);
    })();
    """

    async with AsyncWebCrawler(verbose=True) as crawler:
        # Intentamos primero sin LLM extraction expensive si podemos confiar en regex
        # Pero el código original usaba LLM. Lo mantendremos pero optimizado.
        result = await crawler.arun(target_url, extraction_strategy=llm_strategy, bypass_cache=True, js_code=js_lazy_load) 
        
        image_urls = []
        if result.success:
            # 1. Intentar extracción IA
            try:
                if result.extracted_content:
                    clean = result.extracted_content
                    if "```json" in clean: clean = clean.split("```json")[1].split("```")[0].strip()
                    elif "```" in clean: clean = clean.split("```")[1].split("```")[0].strip()
                    image_urls = json.loads(clean).get("images", [])
            except: pass
            
            # 2. Intentar Regex (Suele ser más efectivo en ZonaTMO)
            if not image_urls and result.html:
                matches = re.findall(r'(https?://(?:img1?\.?tmo\.com|otakuteca\.com|img1tmo\.com)[^"\'\s]+\.(?:webp|jpg|png))', result.html)
                if matches: image_urls = sorted(list(set(matches)))
        
        # Filtrar basura (covers, banners)
        image_urls = [u for u in image_urls if "cover" not in u and "avatar" not in u and "banner" not in u]

        if image_urls:
            log_callback(f"[INFO] Imágenes encontradas: {len(image_urls)}")
            
            final_pdf = output_name
            # Si es descarga individual y no tenemos nombre, intentar adivinar
            if output_name == "zonatmo_chapter.pdf" and result.html:
                 match = re.search(r'<h1[^>]*>(.*?)</h1>', result.html)
                 if match:
                     final_pdf = f"{clean_filename(match.group(1))}.pdf"

            is_path = "/" in final_pdf or "\\" in final_pdf
            await download_and_make_pdf(image_urls, final_pdf, HEADERS_ZONATMO, log_callback, check_cancel, progress_callback, is_path=is_path)
        else:
            log_callback("[ERROR] No se encontraron imágenes.")

async def process_entry(url: str, log_callback: Callable[[str], None], check_cancel: Callable[[], bool], progress_callback: Optional[Callable[[int, int], None]] = None):
    """Router principal: Redirige a la función específica según el dominio."""
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
    elif "zonatmo.com" in url:
        await process_zonatmo(url, log_callback, check_cancel, progress_callback=progress_callback)
    else:
        log_callback("[ERROR] Sitio web no soportado.")
