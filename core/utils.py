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
from PIL import Image
try:
    import img2pdf
except ImportError:
    img2pdf = None

from .config import PDF_FOLDER_NAME, TEMP_FOLDER_NAME, BATCH_SIZE

# --- Reusable HTTP Session (Keep-Alive, Connection Pooling) ---
# A dictionary to hold named sessions for different components (web, bot, etc.)
_http_sessions: dict = {}
_session_lock: asyncio.Lock = None


def _get_session_lock() -> asyncio.Lock:
    """Lazily create the asyncio Lock for session management."""
    global _session_lock
    if _session_lock is None:
        _session_lock = asyncio.Lock()
    return _session_lock


async def get_http_session(name: str = "default", headers: Optional[dict] = None) -> aiohttp.ClientSession:
    """
    Returns a reusable aiohttp.ClientSession for the given name.
    Creates one if it doesn't exist. Sessions use keep-alive and connection pooling.
    
    Args:
        name: A namespace for the session (e.g. 'default', 'bot', 'web').
        headers: Default headers for this session.
    
    Returns:
        An aiohttp.ClientSession instance.
    """
    lock = _get_session_lock()
    async with lock:
        if name not in _http_sessions or _http_sessions[name].closed:
            connector = aiohttp.TCPConnector(
                limit=50,           # Max total connections
                limit_per_host=20,  # Max connections per host
                ttl_dns_cache=300,  # DNS cache TTL (5 min)
                force_close=False,  # Use keep-alive
            )
            timeout = aiohttp.ClientTimeout(total=60, connect=15, sock_read=30)
            session_headers = headers or {}
            _http_sessions[name] = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers=session_headers,
            )
        return _http_sessions[name]


async def close_http_sessions():
    """Gracefully close all reusable HTTP sessions."""
    lock = _get_session_lock()
    async with lock:
        for name, session in list(_http_sessions.items()):
            if not session.closed:
                await session.close()
        _http_sessions.clear()

def clean_filename(text: str) -> str:
    """
    Sanitizes the string to create a valid Windows/Linux file or directory name.
    Strips HTML tags and removes reserved characters `\ / * ? : " < > |`.
    
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
    Downloads a single image asynchronously and saves it to a persistent local path.
    
    Args:
        session (aiohttp.ClientSession): The active asynchronous HTTP session.
        url (str): The direct image URL.
        folder (str): The local directory to save the image (usually a temporary folder).
        index (int): The page order index, used to ensure correct sorting in the PDF structure.
        log_callback (Callable): Callback interface to send errors/logs to the frontend.
        headers (dict): HTTP headers to bypass bot-protections (Cloudflare, User-Agent, etc).
        
    Returns:
        Optional[str]: The absolute path to the downloaded image if successful, else None.
    """
    try:
        # Determine extension
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
                log_callback(f"[ERROR] Failed to download image {index}: Status {resp.status}")
                return None
    except Exception as e:
        log_callback(f"[ERROR] Failed to download image {index}: {str(e)}")
        return None

def create_pdf(image_paths: List[str], output_pdf: str, log_callback: Callable[[str], None]) -> bool:
    """Compiles a list of image paths into a single PDF using img2pdf (if available) or Pillow."""
    if not image_paths:
        log_callback("[WARN] No images to compile into PDF.")
        return False

    final_paths = []
    
    try:
        for path in image_paths:
            try:
                with Image.open(path) as img:
                    ext_lower = os.path.splitext(path)[1].lower()
                    needs_conversion = (
                        img.mode in ("RGBA", "LA") or 
                        (img.mode == "P" and "transparency" in img.info) or
                        ext_lower not in (".jpg", ".jpeg", ".png")
                    )
                    
                    if needs_conversion:
                        new_path = f"{os.path.splitext(path)[0]}_converted.jpg"
                        img.convert("RGB").save(new_path, "JPEG", quality=70)
                        final_paths.append(new_path)
                    else:
                        final_paths.append(path)
            except Exception:
                final_paths.append(path)
        
        if img2pdf:
            with open(output_pdf, "wb") as f:
                f.write(img2pdf.convert(final_paths, rotation=img2pdf.Rotation.ifvalid))
        else:
            raise ImportError("img2pdf not installed")

        try:
            project_root = os.getcwd()
            pdf_root = os.path.join(project_root, PDF_FOLDER_NAME)
            
            if os.path.abspath(output_pdf).startswith(os.path.abspath(pdf_root)):
                 logged_path = os.path.relpath(output_pdf, pdf_root)
            else:
                 logged_path = os.path.basename(output_pdf)
            logged_path = logged_path.replace("\\", "/")
        except:
             logged_path = os.path.basename(output_pdf)

        log_callback(f"[SUCCESS] PDF Generated: {logged_path}")
        return True

    except Exception as e:
        log_callback(f"[ERROR] Failed to save PDF (img2pdf): {e}")
        try:
            log_callback("[INFO] Trying alternative method (Pillow)...")
            images = []
            for path in image_paths:
                try:
                    with Image.open(path) as img:
                        if img.mode in ("RGBA", "P"): img = img.convert("RGB")
                        images.append(img.copy())
                except: pass
                
            if images:
                images[0].save(output_pdf, "PDF", resolution=100.0, save_all=True, append_images=images[1:])
                return True
        except Exception as e2:
             log_callback(f"[ERROR] Alternative method failed: {e2}")
        
        return False

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
    
    async with aiohttp.ClientSession(headers=headers) as session:
        chunk_size = BATCH_SIZE 
        results = []
        for i in range(0, len(image_urls), chunk_size):
            if check_cancel and check_cancel():
                log_callback("[INFO] Process cancelled by user.")
                break
            chunk = image_urls[i:i+chunk_size]
            tasks = [download_image(session, u, temp_folder, i + idx + 1, log_callback) for idx, u in enumerate(chunk)]
            res = await asyncio.gather(*tasks)
            results.extend(res)
            
            if progress_callback:
                progress_callback(min(i + chunk_size, len(image_urls)), len(image_urls))
            
        files = [f for f in results if f]
    
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
