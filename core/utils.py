
import os
import re
import shutil
import asyncio
import aiohttp
from typing import List, Optional, Callable
from PIL import Image
try:
    import img2pdf
except ImportError:
    img2pdf = None

from .config import PDF_FOLDER_NAME, TEMP_FOLDER_NAME, BATCH_SIZE

def clean_filename(text: str) -> str:
    """Sanitizes the filename for Windows/Linux."""
    if not text: return "untitled"
    text = re.sub(r'<[^>]+>', '', text)
    safe = re.sub(r'[\\/*?:"<>|]', "", text).strip()
    return safe if safe else "untitled"

async def download_image(session: aiohttp.ClientSession, url: str, folder: str, index: int, log_callback: Callable[[str], None], headers: dict) -> Optional[str]:
    """Downloads a single image and returns its local path."""
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
                    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                        head, tail = os.path.split(path)
                        new_filename = os.path.splitext(tail)[0] + "_converted.jpg"
                        new_path = os.path.join(head, new_filename)
                        img.convert("RGB").save(new_path, "JPEG", quality=90)
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
                try: os.startfile(os.path.dirname(output_pdf))
                except: pass
                try: os.startfile(output_pdf)
                except: pass
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
    temp_folder = os.path.join(project_root, TEMP_FOLDER_NAME)
    
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
            tasks = [download_image(session, u, temp_folder, i + idx + 1, log_callback, headers) for idx, u in enumerate(chunk)]
            res = await asyncio.gather(*tasks)
            results.extend(res)
            
            if progress_callback:
                progress_callback(min(i + chunk_size, len(image_urls)), len(image_urls))
            
        files = [f for f in results if f]
    
    files.sort()
    
    if files:
        if is_path:
            # Special case where output_name is a full path (e.g. m440 chapter)
            if create_pdf(files, output_name, log_callback):
                pass
        else:
            finalize_pdf_flow(files, output_name, log_callback, temp_folder, open_result=open_result)
            return

    if os.path.exists(temp_folder): shutil.rmtree(temp_folder)
    
    if not is_path:
        log_callback("[DONE] Finished.")
