"""
PDF creation utilities.
"""
from typing import List, Callable
from PIL import Image


def create_pdf(image_paths: List[str], output_pdf: str, log_callback: Callable[[str], None]) -> bool:
    """
    Combines a list of image paths into a single PDF file.
    Converts images to RGB mode (stripping alpha channel) to ensure compatibility.
    
    Args:
        image_paths: List of absolute paths to image files
        output_pdf: Absolute path for output PDF file
        log_callback: Function to call for logging messages
        
    Returns:
        True if PDF was created successfully, False otherwise
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
            log_callback(f"[EXITO] PDF Generado: {output_pdf.split('/')[-1].split('\\\\')[-1]}")
            return True
        except Exception as e:
            log_callback(f"[ERROR] Fallo al guardar PDF: {e}")
            return False
    else:
        log_callback("[ERROR] No hay imágenes válidas para el PDF.")
        return False
