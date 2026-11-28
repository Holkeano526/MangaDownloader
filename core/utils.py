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
import threading
import time
from io import BytesIO

from .config import (PDF_FOLDER_NAME, TEMP_FOLDER_NAME, MEDIA_FOLDER_NAME, CONCURRENCIA_IMAGENES,
                     REINTENTOS_IMAGEN, JPEG_CALIDAD)
from .pdf_worker import preparar_para_pdf as _preparar_para_pdf, generar_pdf as _generar_pdf

# Máximo de bytes UTF-8 para la parte del nombre que sale del título. ext4 admite 255 bytes por
# nombre; se deja margen para sufijos (" (2)", "_<uuid>", " - capítulo") y la extensión.
MAX_BYTES_NOMBRE = 150


def clean_filename(text: str) -> str:
    """
    Sanitizes the string to create a valid Windows/Linux file or directory name.
    Strips HTML tags, reserved characters (backslash, / * ? : " < > |) and control characters,
    and truncates to MAX_BYTES_NOMBRE bytes of UTF-8 (a 90-character Japanese title is ~270 bytes,
    over the 255-byte limit of ext4, and os.makedirs/open failed with ENAMETOOLONG).

    Returns:
        str: A safe file name string. Defaults to 'untitled' if empty.
    """
    if not text: return "untitled"
    text = re.sub(r'<[^>]+>', '', text)
    safe = re.sub(r'[\\/*?:"<>|\x00-\x1f\x7f]', "", text).strip()
    datos = safe.encode("utf-8")
    if len(datos) > MAX_BYTES_NOMBRE:
        safe = datos[:MAX_BYTES_NOMBRE].decode("utf-8", "ignore")
    safe = safe.rstrip(" .")  # Windows no admite nombres que acaben en punto o espacio
    return safe if safe else "untitled"


# Nombres de PDF que se están generando ahora mismo. Dos descargas simultáneas con el mismo título
# (o dos capítulos sueltos de M440) escribían el mismo archivo: uno pisaba o borraba al otro.
_SALIDAS_EN_USO: set = set()
_SALIDAS_LOCK = threading.Lock()


def _reservar_salida(ruta: str) -> str:
    """Devuelve una ruta de salida que ninguna otra descarga en curso esté usando ("x.pdf",
    "x (2).pdf"...) y la marca como en uso. Liberarla con _liberar_salida."""
    base, ext = os.path.splitext(ruta)
    with _SALIDAS_LOCK:
        candidata, n = ruta, 1
        while os.path.normcase(candidata) in _SALIDAS_EN_USO:
            n += 1
            candidata = f"{base} ({n}){ext}"
        _SALIDAS_EN_USO.add(os.path.normcase(candidata))
        return candidata


def _liberar_salida(ruta: str) -> None:
    with _SALIDAS_LOCK:
        _SALIDAS_EN_USO.discard(os.path.normcase(ruta))


def _nombre_pdf_seguro(pdf_name: str) -> str:
    """Nombre de PDF saneado y con longitud acotada (los handlers pueden pasar títulos crudos)."""
    raiz = pdf_name[:-4] if pdf_name.lower().endswith(".pdf") else pdf_name
    return clean_filename(raiz) + ".pdf"

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
                    if await _es_imagen(resp.headers.get("Content-Type", ""), content):
                        await asyncio.to_thread(_escribir, filepath, content)
                        return filepath
                    # Una página HTML (Cloudflare, error) con 200 acababa dentro del PDF y
                    # rompía img2pdf: se trata como fallo reintentable.
                    motivo = f"not an image ({resp.headers.get('Content-Type', '?')}, {len(content)} bytes)"
                elif resp.status in (403, 404, 410):
                    log_callback(f"[ERROR] Failed to download image {index}: Status {resp.status}")
                    return None
                else:
                    motivo = f"Status {resp.status}"
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            motivo = type(e).__name__
        if intento < REINTENTOS_IMAGEN:
            log_callback(f"[WARN] Image {index} got {motivo}. Retrying {intento}/{REINTENTOS_IMAGEN - 1}...")
            await asyncio.sleep(1.5 * intento)
    log_callback(f"[ERROR] Failed to download image {index} after {REINTENTOS_IMAGEN} attempts ({motivo})")
    return None


# Firmas de los formatos que sirven los sitios (JPEG, PNG, GIF, BMP, WebP, AVIF/HEIC).
_FIRMAS_IMAGEN = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a", b"BM")


def _verificar_con_pillow(datos: bytes) -> bool:
    try:
        from PIL import Image
        with Image.open(BytesIO(datos)) as img:
            img.verify()
        return True
    except Exception:
        return False


async def _es_imagen(content_type: str, datos: bytes) -> bool:
    """Comprobación barata de que la respuesta es una imagen (no una página de error)."""
    if not datos:
        return False
    tipo = content_type.split(";")[0].strip().lower()
    if tipo.startswith("text/") or tipo in ("application/json", "application/xhtml+xml"):
        return False
    if datos.startswith(_FIRMAS_IMAGEN):
        return True
    if datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
        return True
    if datos[4:8] == b"ftyp":  # AVIF/HEIF
        return True
    # Formato raro: que decida Pillow (en un hilo, no bloquea el bucle)
    return await asyncio.to_thread(_verificar_con_pillow, datos)


def limpiar_temporales_huerfanos(max_horas: float = 6) -> int:
    """Borra subcarpetas de la carpeta temporal con más de `max_horas` sin modificar.

    Quedan si el proceso muere a mitad de una descarga (reinicio, OOM). Pensado para llamarse
    al arrancar el bot. Devuelve cuántas carpetas se borraron.
    """
    raiz = os.path.join(os.getcwd(), TEMP_FOLDER_NAME)
    if not os.path.isdir(raiz):
        return 0
    limite = time.time() - max_horas * 3600
    borradas = 0
    for nombre in os.listdir(raiz):
        ruta = os.path.join(raiz, nombre)
        try:
            if os.path.isdir(ruta) and os.path.getmtime(ruta) < limite:
                shutil.rmtree(ruta, ignore_errors=True)
                borradas += 1
        except OSError:
            pass
    return borradas


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


def carpeta_media() -> str:
    """Carpeta donde se guardan los vídeos descargados (se crea si no existe)."""
    ruta = os.path.join(os.getcwd(), MEDIA_FOLDER_NAME)
    os.makedirs(ruta, exist_ok=True)
    return ruta


def nombre_media(titulo: str, extension: str, indice: Optional[int] = None) -> str:
    """Nombre de archivo para un vídeo: título limpio + extensión (y « - N» si hay varios)."""
    base = clean_filename(titulo)[:180]
    if indice is not None:
        base = f"{base} - {indice}"
    return f"{base}.{extension.lstrip('.')}"


async def descargar_archivo(url: str, destino: str, headers: dict, log_callback: Callable[[str], None],
                            check_cancel: Optional[Callable[[], bool]] = None,
                            progreso_cada: int = 10) -> Optional[str]:
    """Descarga un archivo grande (vídeo) por streaming, con reintentos y cancelación.

    No se guarda en memoria: escribe en `<destino>.part` y renombra al terminar. Avisa del
    progreso cada `progreso_cada` % para que el embed del bot no se quede parado. Devuelve la
    ruta final o None. El aviso `[SUCCESS] File Generated: ...` es el que usa el bot para
    entregar el archivo (igual que `[SUCCESS] PDF Generated:` con los PDF).
    """
    os.makedirs(os.path.dirname(destino) or ".", exist_ok=True)
    parcial = destino + ".part"
    timeout = aiohttp.ClientTimeout(total=None, connect=20, sock_read=120)
    motivo = "sin respuesta"

    for intento in range(1, REINTENTOS_IMAGEN + 1):
        hecho = 0
        try:
            async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        motivo = f"HTTP {resp.status}"
                        if resp.status in (403, 404, 410):
                            break  # no tiene sentido reintentar
                    else:
                        total = int(resp.headers.get("Content-Length") or 0)
                        siguiente = progreso_cada
                        with open(parcial, "wb") as f:
                            async for trozo in resp.content.iter_chunked(1024 * 1024):
                                if check_cancel and check_cancel():
                                    f.close()
                                    os.remove(parcial)
                                    log_callback("[INFO] Descarga de vídeo cancelada.")
                                    return None
                                f.write(trozo)
                                hecho += len(trozo)
                                if total:
                                    pct = int(hecho * 100 / total)
                                    if pct >= siguiente:
                                        log_callback(f"[INFO] Descargando vídeo: {pct}%")
                                        siguiente = (pct // progreso_cada + 1) * progreso_cada
                        if total and hecho < total:
                            motivo = f"incompleto ({hecho}/{total} bytes)"
                        else:
                            os.replace(parcial, destino)
                            log_callback(f"[SUCCESS] File Generated: {os.path.abspath(destino)}")
                            return destino
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            motivo = type(e).__name__

        if os.path.exists(parcial):
            try:
                os.remove(parcial)
            except OSError:
                pass
        if intento < REINTENTOS_IMAGEN:
            log_callback(f"[WARN] Descarga de vídeo falló ({motivo}); reintento {intento}/{REINTENTOS_IMAGEN - 1}...")
            await asyncio.sleep(2 * intento)

    log_callback(f"[ERROR] No se pudo descargar el vídeo: {motivo}")
    return None


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
    
    output_pdf = _reservar_salida(os.path.join(pdf_dir, _nombre_pdf_seguro(pdf_name)))
    log_callback(f"[INFO] Generating PDF: {os.path.basename(output_pdf)}")
    
    try:
        ok = create_pdf(image_paths, output_pdf, log_callback)
    finally:
        _liberar_salida(output_pdf)
    if ok:
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
    
    output_pdf = _reservar_salida(os.path.join(pdf_dir, _nombre_pdf_seguro(pdf_name)))
    log_callback(f"[INFO] Generating PDF: {os.path.basename(output_pdf)}")
    
    # Run CPU-bound PDF creation in a separate thread
    try:
        success = await asyncio.to_thread(create_pdf, image_paths, output_pdf, log_callback)
    finally:
        _liberar_salida(output_pdf)
    
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

    # try/finally: si algo lanza una excepción (red, disco, cancelación de la tarea) la carpeta
    # temporal no se queda huérfana.
    try:
        files = await descargar_imagenes(image_urls, temp_folder, headers, log_callback, check_cancel, progress_callback)
        if check_cancel and check_cancel():
            # Cancelado: no se genera un PDF a medias
            log_callback("[INFO] Process cancelled by user.")
            return
        if len(files) < len(image_urls):
            log_callback(f"[WARN] {len(image_urls) - len(files)} de {len(image_urls)} páginas no se pudieron descargar.")

        # Sin files.sort(): descargar_imagenes ya devuelve las páginas en orden, y ordenar por texto
        # ponía "1000.jpg" antes que "101.jpg" en galerías de más de 999 páginas.
        if files:
            if is_path:
                # Special case where output_name is a full path (e.g. m440 chapter)
                salida = _reservar_salida(output_name)
                try:
                    await asyncio.to_thread(create_pdf, files, salida, log_callback)
                finally:
                    _liberar_salida(salida)
            else:
                await async_finalize_pdf_flow(files, output_name, log_callback, temp_folder, open_result=open_result)
                return

        if not is_path:
            log_callback("[DONE] Finished.")
    finally:
        shutil.rmtree(temp_folder, ignore_errors=True)
