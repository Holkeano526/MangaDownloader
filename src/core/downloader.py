"""
Image downloading and PDF orchestration utilities.
"""
import asyncio
import os
import shutil
from typing import List, Optional, Callable
import aiohttp

from ..config import BATCH_SIZE, TEMP_FOLDER_NAME
from .pdf_creator import create_pdf


async def download_image(
    session: aiohttp.ClientSession,
    url: str,
    folder: str,
    index: int,
    log_callback: Callable[[str], None],
    headers: dict
) -> Optional[str]:
    """
    Downloads a single image from a URL and saves it to the specified folder.
    
    Args:
        session: aiohttp session for making requests
        url: URL of the image to download
        folder: Folder to save the image in
        index: Index number for the image filename
        log_callback: Function to call for logging messages
        headers: HTTP headers to use for the request
        
    Returns:
        File path if successful, None otherwise
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
                return None
    except Exception as e:
        log_callback(f"[ERROR] Fallo al descargar imagen {index}: {str(e)}")
        return None


async def download_and_make_pdf(
    image_urls: List[str],
    output_name: str,
    headers: dict,
    log_callback: Callable[[str], None],
    check_cancel: Callable[[], bool],
    progress_callback: Optional[Callable[[int, int], None]] = None,
    is_path: bool = False
) -> None:
    """
    Orchestration function: Downloads images in chunks -> Creates PDF -> Cleans up.
    
    Args:
        image_urls: List of image URLs to download
        output_name: Filename (e.g., 'manga.pdf') or absolute path if is_path=True
        headers: HTTP headers to use for requests
        log_callback: Function to call for logging messages
        check_cancel: Function that returns True if user cancelled
        progress_callback: Optional function to call with (current, total) progress
        is_path: If True, output_name is treated as a full path
    """
    current_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    temp_folder = os.path.join(current_dir, TEMP_FOLDER_NAME)
    
    # Clean/Create temp folder
    if os.path.exists(temp_folder): 
        shutil.rmtree(temp_folder)
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
        # Determine final output path
        if is_path:
            output_pdf = output_name
            os.makedirs(os.path.dirname(output_pdf), exist_ok=True)
        else:
            from ..config import PDF_FOLDER_NAME
            pdf_dir = os.path.join(current_dir, "output", PDF_FOLDER_NAME)
            os.makedirs(pdf_dir, exist_ok=True)
            output_pdf = os.path.join(pdf_dir, output_name)
            
        if create_pdf(files, output_pdf, log_callback):
             # Try to open the file location for the user
             if os.path.exists(output_pdf):
                 try: os.startfile(os.path.dirname(output_pdf))
                 except: pass
                 try: os.startfile(output_pdf)
                 except: pass
    
    # Cleanup
    if os.path.exists(temp_folder): 
        shutil.rmtree(temp_folder)
    log_callback("[HECHO] Finalizado.")
