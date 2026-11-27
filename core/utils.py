"""
Module: core.utils
Description: Provides shared utilities for file system management, 
asynchronous image downloading, and PDF compilation for the Manga Downloader.
"""
import os
import re
import shutil
import asyncio
import aiohttp
import subprocess
import sys
import uuid
from typing import List, Optional, Callable
import json

from .config import PDF_FOLDER_NAME, TEMP_FOLDER_NAME, CONCURRENCIA_IMAGENES, REINTENTOS_IMAGEN, JPEG_CALIDAD
from .pdf_worker import preparar_para_pdf as _preparar_para_pdf, generar_pdf as _generar_pdf

def clean_filename(text: str) -> str:
    """
    Sanitizes the string to create a valid Windows/Linux file or directory name.
    Strips HTML tags and removes reserved characters (backslash, / * ? : " < > |).
    
    Args:
        text (str): The raw title string to be sanitized.
        
    Returns:
        str: A safe file name string. Defaults to 'untitled' if empty.
    """
    if not text: return "untitled"
    text = re.sub(r'<[^>]+>', '', text)
    safe = re.sub(r'[\\/*?:"<>|]', "", text).strip()
    return safe if safe else "untitled"

async def download_image(session: aiohttp.ClientSession, url: str, folder: str, index: int, log_callback: Callable[[str], None], headers: Optional[dict] = None) -> Optional[str]:
    """
    Descarga una imagen con reintentos (429/5xx/timeouts, espera creciente).
    Antes un fallo dejaba la página fuera del PDF sin reintentar.
    Devuelve la ruta local o None si falla definitivamente.
    """
    ext = ".jpg"
    if ".webp" in url: ext = ".webp"
    elif ".png" in url: ext = ".png"
    elif ".jpeg" in url: ext = ".jpeg"
    elif ".avif" in url: ext = ".avif"
    filepath = os.path.join(folder, f"{index:03d}{ext}")

    for intento in range(1, REINTENTOS_IMAGEN + 1):
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    content = await resp.read()
                    await asyncio.to_thread(_escribir, filepath, content)
                    return filepath
                if resp.status in (403, 404, 410):
                    log_callback(f"[ERROR] Failed to download image {index}: Status {resp.status}")
                    return None
                motivo = f"Status {resp.status}"
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            motivo = type(e).__name__
        if intento < REINTENTOS_IMAGEN:
            log_callback(f"[WARN] Image {index} got {motivo}. Retrying {intento}/{REINTENTOS_IMAGEN - 1}...")
            await asyncio.sleep(1.5 * intento)
    log_callback(f"[ERROR] Failed to download image {index} after {REINTENTOS_IMAGEN} attempts ({motivo})")
    return None


def _escribir(ruta: str, datos: bytes) -> None:
    with open(ruta, "wb") as f:
        f.write(datos)


async def descargar_imagenes(urls: List[str], folder: str, headers: dict, log_callback: Callable[[str], None],
                             check_cancel: Optional[Callable[[], bool]] = None,
                             progress_callback: Optional[Callable[[int, int], None]] = None,
                             concurrencia: int = CONCURRENCIA_IMAGENES, convertir: bool = True) -> List[str]:
    """
    Descarga todas las imágenes con un pool de `concurrencia` conexiones: en cuanto
    termina una empieza la siguiente (antes eran tandas de 10 que esperaban a la más lenta).
    Devuelve las rutas en el orden de páginas (las fallidas se omiten).
    """
    sem = asyncio.Semaphore(concurrencia)
    sem_cpu = asyncio.Semaphore(_cpus_disponibles())
    hechas = 0
    total = len(urls)
    timeout = aiohttp.ClientTimeout(total=120, connect=15, sock_read=60)
    connector = aiohttp.TCPConnector(limit=concurrencia * 2, limit_per_host=concurrencia, ttl_dns_cache=300)

    async with aiohttp.ClientSession(headers=headers, timeout=timeout, connector=connector) as session:
        async def una(idx: int, url: str) -> Optional[str]:
            nonlocal hechas
            async with sem:
                if check_cancel and check_cancel():
                    return None
                ruta = await download_image(session, url, folder, idx, log_callback)
            if ruta and convertir:
                # Convertir para el PDF mientras se descargan las demás (la CPU estaba
                # parada esperando a la red). Limitado a las CPU reales del contenedor.
                async with sem_cpu:
                    ruta = await asyncio.to_thread(preparar_para_pdf, ruta)
            hechas += 1
            if progress_callback:
                progress_callback(hechas, total)
            return ruta

        resultados = await asyncio.gather(*(una(i + 1, u) for i, u in enumerate(urls)))
    return [r for r in resultados if r]


def _cpus_disponibles() -> int:
    """CPU que el contenedor puede usar de verdad (cgroup cpu.max), no las del host.
    Con --cpus 1 y 4 hilos, el kernel frena el proceso (CFS throttling): medido
    9,2 s frente a 3,1 s al generar un PDF de 89 páginas."""
    try:
        with open("/sys/fs/cgroup/cpu.max") as f:
            cuota, periodo = f.read().split()
        if cuota != "max":
            return max(1, int(int(cuota) / int(periodo)))
    except (OSError, ValueError):
        pass
    return max(1, min(4, os.cpu_count() or 1))

def preparar_para_pdf(path: str) -> str:
    """JPEG/PNG van tal cual; WebP/AVIF/transparencias se convierten a JPEG (img2pdf no los admite)."""
    return _preparar_para_pdf(path, JPEG_CALIDAD)


_PDF_WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdf_worker.py")
_PDF_WORKER_TIMEOUT = 1800  # 30 min: un PDF de cientos de páginas WebP en un NAS lento


def _ruta_para_log(output_pdf: str) -> str:
    try:
        pdf_root = os.path.join(os.getcwd(), PDF_FOLDER_NAME)
        if os.path.abspath(output_pdf).startswith(os.path.abspath(pdf_root)):
            logged_path = os.path.relpath(output_pdf, pdf_root)
        else:
            logged_path = os.path.basename(output_pdf)
        return logged_path.replace("\\", "/")
    except Exception:
        return os.path.basename(output_pdf)


def create_pdf(image_paths: List[str], output_pdf: str, log_callback: Callable[[str], None]) -> bool:
    """Compiles a list of image paths into a single PDF using img2pdf (if available) or Pillow.

    Se genera en un proceso aparte (core/pdf_worker.py) para que la memoria del PDF
    (cientos de MB) vuelva al sistema al terminar; ver la explicación en pdf_worker.
    """
    if not image_paths:
        log_callback("[WARN] No images to compile into PDF.")
        return False

    pedido = json.dumps({"paths": image_paths, "output": output_pdf,
                         "calidad": JPEG_CALIDAD, "hilos": _cpus_disponibles()})
    try:
        proc = subprocess.run([sys.executable, _PDF_WORKER], input=pedido.encode("utf-8"),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=_PDF_WORKER_TIMEOUT)
        for linea in proc.stdout.decode("utf-8", "replace").splitlines():
            if linea.strip():
                log_callback(linea)
        if proc.returncode != 0 and proc.stderr:
            ultima = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-1:]
            if ultima:
                log_callback(f"[ERROR] PDF worker: {ultima[0][:300]}")
        ok = proc.returncode == 0
    except subprocess.TimeoutExpired:
        log_callback(f"[ERROR] PDF worker: more than {_PDF_WORKER_TIMEOUT}s, aborted.")
        ok = False
    except OSError as e:
        # No se pudo lanzar el proceso (raro): se genera aquí, como antes
        log_callback(f"[WARN] PDF worker unavailable ({e}); generating in-process.")
        ok = _generar_pdf(image_paths, output_pdf, log_callback,
                          calidad=JPEG_CALIDAD, hilos=_cpus_disponibles())

    if ok:
        log_callback(f"[SUCCESS] PDF Generated: {_ruta_para_log(output_pdf)}")
    return ok

def _open_file_or_folder(file_path: str):
    """
    Opens a file or its containing folder, with multiplatform support.
    Falls back gracefully on headless environments (Docker, Linux servers).
    """
    try:
        if sys.platform == "win32":
            os.startfile(os.path.dirname(file_path))
            os.startfile(file_path)
        elif sys.platform == "darwin":  # macOS
            subprocess.call(["open", os.path.dirname(file_path)])
            subprocess.call(["open", file_path])
        else:  # Linux and others
            # In Docker/headless Linux, xdg-open might fail - we gracefully ignore
            try:
                subprocess.call(["xdg-open", file_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (FileNotFoundError, OSError):
                pass  # Headless environment, cannot open files
    except Exception:
        pass  # Never crash on file-opening failure


def finalize_pdf_flow(image_paths: List[str], pdf_name: str, log_callback: Callable[[str], None], 
                      temp_dir: Optional[str] = None, open_result: bool = True):
    """
    Creates PDF, Opens it/Folder (if open_result is True), and Cleans up temp dir.
    """
    project_root = os.getcwd() 
    pdf_dir = os.path.join(project_root, PDF_FOLDER_NAME)
    os.makedirs(pdf_dir, exist_ok=True)
    
    output_pdf = os.path.join(pdf_dir, pdf_name)
    log_callback(f"[INFO] Generating PDF: {pdf_name}")
    
    if create_pdf(image_paths, output_pdf, log_callback):
        if open_result:
            if os.path.exists(output_pdf):
                _open_file_or_folder(output_pdf)
        log_callback("[DONE] Finished.")
    else:
        log_callback("[ERROR] Could not create PDF.")

    if temp_dir and os.path.exists(temp_dir):
        try:
            shutil.rmtree(temp_dir)
        except: pass


async def async_finalize_pdf_flow(image_paths: List[str], pdf_name: str, log_callback: Callable[[str], None], 
                                   temp_dir: Optional[str] = None, open_result: bool = True):
    """
    Async version of finalize_pdf_flow. Runs CPU-bound create_pdf in a thread
    to avoid blocking the event loop, then opens the file and cleans up.
    """
    project_root = os.getcwd() 
    pdf_dir = os.path.join(project_root, PDF_FOLDER_NAME)
    os.makedirs(pdf_dir, exist_ok=True)
    
    output_pdf = os.path.join(pdf_dir, pdf_name)
    log_callback(f"[INFO] Generating PDF: {pdf_name}")
    
    # Run CPU-bound PDF creation in a separate thread
    success = await asyncio.to_thread(create_pdf, image_paths, output_pdf, log_callback)
    
    if success:
        if open_result:
            if os.path.exists(output_pdf):
                _open_file_or_folder(output_pdf)
        log_callback("[DONE] Finished.")
    else:
        log_callback("[ERROR] Could not create PDF.")

    if temp_dir and os.path.exists(temp_dir):
        try:
            shutil.rmtree(temp_dir)
        except: pass

async def download_and_make_pdf(image_urls: List[str], output_name: str, headers: dict, 
                                log_callback: Callable[[str], None], check_cancel: Callable[[], bool], 
                                progress_callback: Optional[Callable[[int, int], None]] = None, 
                                is_path: bool = False, open_result: bool = True) -> None:
    """
    Orchestration function: Downloads images in chunks -> Creates PDF/Folder -> Cleans up.
    """
    project_root = os.getcwd()
    
    # Generate unique temp folder using output name and UUID
    base_name = os.path.basename(output_name)
    safe_name = clean_filename(base_name.replace('.pdf', ''))
    unique_id = uuid.uuid4().hex[:8]
    temp_folder = os.path.join(project_root, TEMP_FOLDER_NAME, f"{safe_name}_{unique_id}")
    
    # Clean/Create temp folder
    if os.path.exists(temp_folder): shutil.rmtree(temp_folder)
    os.makedirs(temp_folder, exist_ok=True)
    
    files = []
    
    files = await descargar_imagenes(image_urls, temp_folder, headers, log_callback, check_cancel, progress_callback)
    if check_cancel and check_cancel():
        # Cancelado: no se genera un PDF a medias
        log_callback("[INFO] Process cancelled by user.")
        shutil.rmtree(temp_folder, ignore_errors=True)
        return
    if len(files) < len(image_urls):
        log_callback(f"[WARN] {len(image_urls) - len(files)} de {len(image_urls)} páginas no se pudieron descargar.")

    files.sort()
    
    if files:
        if is_path:
            # Special case where output_name is a full path (e.g. m440 chapter)
            success = await asyncio.to_thread(create_pdf, files, output_name, log_callback)
        else:
            await async_finalize_pdf_flow(files, output_name, log_callback, temp_folder, open_result=open_result)
            return

    if os.path.exists(temp_folder): shutil.rmtree(temp_folder)
    
    if not is_path:
        log_callback("[DONE] Finished.")
