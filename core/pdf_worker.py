"""
Module: core.pdf_worker
Genera un PDF a partir de imágenes. core.utils.create_pdf lo lanza como proceso
aparte ("python core/pdf_worker.py"), no en un hilo del bot.

Por qué un proceso aparte: armar el PDF toca cientos de MB (un PDF de 347 MB con
185 páginas llevó el proceso del bot a 1,57 GB de pico). En un hilo, glibc deja
esa memoria en los "arenas" de los hilos y no la devuelve al sistema: el 26/11/2026
el bot seguía con 1,7 GB (RAM + swap) dos horas después, sin hacer nada. Al acabar
el proceso hijo, el sistema recupera toda su memoria.

No importa nada del paquete core (core/__init__ carga los handlers y Playwright):
solo Pillow e img2pdf, para que arrancar el proceso sea rápido.

Protocolo: JSON por stdin {"paths": [...], "output": "...", "calidad": 70, "hilos": 2};
los mensajes de log salen por stdout (una línea cada uno); código de salida 0 = PDF creado.
"""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, List

from PIL import Image

try:
    import img2pdf
except ImportError:
    img2pdf = None


def preparar_para_pdf(path: str, calidad: int = 70) -> str:
    """JPEG/PNG van tal cual; WebP/AVIF/transparencias se convierten a JPEG (img2pdf no los admite)."""
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
                img.convert("RGB").save(new_path, "JPEG", quality=calidad)
                return new_path
    except Exception:
        pass
    return path


def generar_pdf(image_paths: List[str], output_pdf: str, log: Callable[[str], None],
                calidad: int = 70, hilos: int = 1) -> bool:
    """Convierte lo que haga falta y escribe el PDF con img2pdf (o Pillow si img2pdf falla).

    Se escribe en `<output_pdf>.part` y se renombra al terminar: si falla a mitad no queda
    un PDF corrupto con el nombre final.
    """
    parcial = output_pdf + ".part"
    final_paths = list(image_paths)
    try:
        # Decodificar WebP/AVIF es lo caro: en paralelo (Pillow libera el GIL al decodificar)
        with ThreadPoolExecutor(max_workers=max(1, hilos)) as ex:
            final_paths = list(ex.map(lambda p: preparar_para_pdf(p, calidad), image_paths))
        if not img2pdf:
            raise ImportError("img2pdf not installed")
        # outputstream: escribe al archivo según genera. Sin él, img2pdf devuelve el PDF
        # entero como bytes y el pico es ~3 veces el tamaño del PDF (medido: 1159 MB para
        # un PDF de 387 MB, frente a 430 MB así).
        with open(parcial, "wb") as f:
            img2pdf.convert(final_paths, rotation=img2pdf.Rotation.ifvalid, outputstream=f)
        os.replace(parcial, output_pdf)
        return True
    except Exception as e:
        log(f"[ERROR] Failed to save PDF (img2pdf): {e}")
        try:
            log("[INFO] Trying alternative method (Pillow)...")
            if _pdf_con_pillow(final_paths, parcial):
                os.replace(parcial, output_pdf)
                return True
        except Exception as e2:
            log(f"[ERROR] Alternative method failed: {e2}")
        return False
    finally:
        if os.path.exists(parcial):
            try:
                os.remove(parcial)
            except OSError:
                pass


def _pdf_con_pillow(paths: List[str], salida: str) -> bool:
    """PDF página a página (save con append=True): solo hay una imagen decodificada en memoria
    a la vez, en vez de todas (cientos de MB en galerías grandes). Las ilegibles se omiten."""
    primera = True
    for path in paths:
        try:
            img = Image.open(path)
            img.load()
        except Exception:
            continue
        with img:
            pagina = img.convert("RGB") if img.mode in ("RGBA", "LA", "P") else img
            pagina.save(salida, "PDF", resolution=100.0, append=not primera)
        primera = False
    return not primera


def _main() -> int:
    pedido = json.loads(sys.stdin.buffer.read().decode("utf-8"))

    def log(msg: str) -> None:
        print(msg, flush=True)

    ok = generar_pdf(pedido["paths"], pedido["output"], log,
                     calidad=int(pedido.get("calidad", 70)), hilos=int(pedido.get("hilos", 1)))
    return 0 if ok else 1


if __name__ == "__main__":
    # Rutas y títulos con acentos: stdout en UTF-8 aunque la consola diga otra cosa (Windows)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(_main())
