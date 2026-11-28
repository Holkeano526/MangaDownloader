"""
X / Twitter: vídeos de tuits públicos.

yt-dlp solo se usa para leer el tuit (sin iniciar sesión ni cookies) y obtener las variantes MP4
directas que publica X ("http-<bitrate>"); la descarga la hace utils.descargar_archivo (streaming,
progreso, reintentos y cancelación), así que no hace falta ffmpeg. Un tuit con varios vídeos
genera un MP4 por vídeo. Los tuits que piden iniciar sesión (contenido sensible, cuentas
protegidas) no se descargan: se avisa.
"""
import asyncio
import os
import re
from typing import Callable, List, Optional
from urllib.parse import urlparse, urlunparse

from .base import BaseSiteHandler
from ..config import MEDIA_FOLDER_NAME
from ..utils import clean_filename, descargar_archivo

# Espejos que reescriben el enlace para las previsualizaciones (fxtwitter, vxtwitter...): se
# normalizan a x.com antes de pasárselos a yt-dlp.
DOMINIOS = ["x.com", "twitter.com", "fxtwitter.com", "vxtwitter.com", "fixupx.com", "fixvx.com"]
CARPETA = "twitter"  # dentro de Media/; la limpieza de 30 días del NAS la incluye

_STATUS = re.compile(r"/(?:i/web/|[^/]+/)?status(?:es)?/(\d+)")


def enlace_canonico(url: str) -> str:
    """Mismo tuit en x.com, sin parámetros de seguimiento (?s=20&t=...)."""
    p = urlparse(url.strip())
    return urlunparse(("https", "x.com", p.path, "", "", ""))


def id_tuit(url: str) -> Optional[str]:
    m = _STATUS.search(urlparse(url).path)
    return m.group(1) if m else None


def mejor_mp4(formatos: list) -> Optional[dict]:
    """La variante MP4 directa (no HLS) de mayor calidad. X no informa del códec en las directas
    (vcodec None), así que solo se descartan las que dicen explícitamente que no tienen vídeo."""
    directos = [f for f in formatos or []
                if f.get("url") and f.get("ext") == "mp4"
                and f.get("protocol") in ("https", "http")
                and f.get("vcodec") != "none"]
    if not directos:
        return None
    return max(directos, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))


def motivo_error(mensaje: str) -> str:
    """Traduce los errores de yt-dlp a algo que el usuario entienda."""
    m = mensaje.lower()
    if "nsfw" in m or "log in" in m or "login" in m or "authentication" in m or "age-restricted" in m:
        return ("X pide iniciar sesión para ver este tuit (contenido sensible o cuenta protegida). "
                "El bot solo descarga tuits públicos.")
    if "no video could be found" in m or "no video" in m:
        # Sin sesión, X tampoco enseña el vídeo de los tuits «Age-restricted adult content»:
        # yt-dlp lo ve como un tuit sin vídeo y no hay forma de distinguirlo.
        return ("No encontré vídeo en ese tuit. Si en X sale «Age-restricted adult content», es porque X "
                "oculta el vídeo sin iniciar sesión, y el bot solo descarga tuits públicos sin restricción.")
    if "unavailable" in m or "not found" in m or "404" in m or "deleted" in m:
        return "El tuit no existe o ya no está disponible."
    if "rate" in m and "limit" in m:
        return "X está limitando las peticiones; prueba de nuevo en unos minutos."
    return f"No se pudo leer el tuit: {mensaje.replace('ERROR: ', '')[:300]}"


def _extraer(url: str) -> dict:
    import yt_dlp  # aquí y no arriba: solo lo necesita este handler
    opciones = {"quiet": True, "no_warnings": True, "skip_download": True,
                "socket_timeout": 20, "noplaylist": False}
    with yt_dlp.YoutubeDL(opciones) as y:
        return y.extract_info(url, download=False)


class TwitterHandler(BaseSiteHandler):
    """Vídeos de tuits públicos de X/Twitter."""

    @staticmethod
    def get_supported_domains() -> list:
        return DOMINIOS

    async def process(self, url: str, log_callback: Callable[[str], None], check_cancel: Callable[[], bool],
                      progress_callback: Optional[Callable[[int, int], None]] = None) -> None:
        if not id_tuit(url):
            log_callback("[ERROR] Eso no es el enlace de un tuit (debe contener /status/<número>).")
            return
        url = enlace_canonico(url)
        log_callback("[INIT] Leyendo el tuit de X...")
        try:
            info = await asyncio.to_thread(_extraer, url)
        except Exception as e:  # yt_dlp.utils.DownloadError y similares
            log_callback(f"[ERROR] {motivo_error(str(e))}")
            return

        entradas: List[dict] = [e for e in (info.get("entries") or [info]) if e]
        videos = [(e, mejor_mp4(e.get("formats"))) for e in entradas]
        videos = [(e, f) for e, f in videos if f]
        if not videos:
            log_callback("[ERROR] Ese tuit no tiene un vídeo descargable (solo texto, imágenes o un formato no compatible).")
            return

        autor = info.get("uploader_id") or entradas[0].get("uploader_id") or "x"
        tuit = id_tuit(url)
        log_callback(f"[INFO] Tuit de @{autor}: {len(videos)} vídeo(s)")
        carpeta = os.path.join(os.getcwd(), MEDIA_FOLDER_NAME, CARPETA)
        for n, (entrada, formato) in enumerate(videos, start=1):
            if check_cancel():
                log_callback("[INFO] Process cancelled by user.")
                return
            sufijo = f"_{n}" if len(videos) > 1 else ""
            destino = os.path.join(carpeta, clean_filename(f"{autor}_{tuit}{sufijo}") + ".mp4")
            if len(videos) > 1:
                log_callback(f"[INFO] Vídeo {n} de {len(videos)} ({formato.get('height') or '?'}p)")
            await descargar_archivo(formato["url"], destino, formato.get("http_headers") or {},
                                    log_callback, check_cancel)
