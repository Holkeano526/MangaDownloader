"""
Erome — álbumes de fotos y vídeos (a menudo de creadoras de OnlyFans).

  Álbum:  https://es.erome.com/a/<id>
  Perfil: https://es.erome.com/<usuario>   (lista de álbumes)

La ficha trae los vídeos en `<source src="...mp4">`. Solo se descargan los vídeos (MP4 en la
carpeta Media); las fotos del álbum se ignoran.
"""
import html
import os
import re
from typing import Callable, List, Optional
from urllib.parse import urljoin

import aiohttp

from .base import BaseSiteHandler
from .. import config
from ..utils import descargar_archivo, carpeta_media, nombre_media

WEB = "https://es.erome.com"
CABECERAS = {"User-Agent": config.USER_AGENT, "Referer": f"{WEB}/"}
MAX_ALBUMES = 60
_RE_ALBUM = re.compile(r"/a/([A-Za-z0-9]+)")
_RE_IMG = re.compile(r'<div class="img" data-src="(https://[^"]+)"')
_RE_IMG_ALT = re.compile(r'data-src="(https://s\d+\.erome\.com/[^"]+\.(?:jpe?g|png|webp|gif)[^"]*)"', re.I)
_RE_VIDEO = re.compile(r'<source src="(https://v\d+\.erome\.com/[^"]+\.mp4[^"]*)"', re.I)
_RE_TITULO = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)


def _limpio(texto: str) -> str:
    texto = re.sub(r"<[^>]+>", " ", texto or "")
    texto = html.unescape(texto)
    texto = re.sub(r"[\x00-\x1f\x7f]+", " ", texto)  # el alt trae caracteres de control
    return re.sub(r"\s+", " ", texto).strip()


def _sin_duplicados(urls: List[str]) -> List[str]:
    return list(dict.fromkeys(urls))


async def _get(s: aiohttp.ClientSession, url: str) -> str:
    async with s.get(url) as r:
        if r.status != 200:
            return ""
        return await r.text(errors="replace")


def titulo_de(pagina: str, respaldo: str = "") -> str:
    m = _RE_TITULO.search(pagina)
    if m and _limpio(m.group(1)):
        return _limpio(m.group(1))
    m = re.search(r"<title[^>]*>(.*?)</title>", pagina, re.S | re.I)
    return _limpio(m.group(1)) if m else respaldo


def imagenes_de(pagina: str) -> List[str]:
    urls = _RE_IMG.findall(pagina) or _RE_IMG_ALT.findall(pagina)
    # Solo imágenes (por si algún data-src apunta a un vídeo o a un avatar)
    return _sin_duplicados(u for u in urls if re.search(r"\.(?:jpe?g|png|webp|gif)(?:\?|$)", u, re.I))


def videos_de(pagina: str) -> List[str]:
    return _sin_duplicados(_RE_VIDEO.findall(pagina))


def albumes_de(pagina: str) -> List[str]:
    """Enlaces de álbum de una página de perfil (o de cualquier listado)."""
    enlaces = [urljoin(WEB + "/", u) for u in re.findall(r'href="([^"]*/a/[A-Za-z0-9]+)"', pagina)]
    return list(dict.fromkeys(enlaces))[:MAX_ALBUMES]


class EromeHandler(BaseSiteHandler):
    """Handler para erome.com (fotos → PDF, vídeos → MP4)."""

    @staticmethod
    def get_supported_domains() -> list:
        return ["erome.com"]

    async def process(self, url: str, log_callback: Callable[[str], None], check_cancel: Callable[[], bool],
                      progress_callback: Optional[Callable[[int, int], None]] = None):
        log_callback("[INIT] Procesando Erome...")
        timeout = aiohttp.ClientTimeout(total=40, connect=10)
        async with aiohttp.ClientSession(headers=CABECERAS, timeout=timeout) as s:
            pagina = await _get(s, url)
        if not pagina:
            log_callback("[ERROR] Erome no respondió (álbum borrado o sitio caído).")
            return None

        if not _RE_ALBUM.search(url):
            # Perfil o listado: devolver los álbumes para encolarlos
            albumes = albumes_de(pagina)
            if not albumes:
                log_callback("[ERROR] No encontré álbumes en esa página de Erome.")
                return None
            log_callback(f"[INFO] Perfil de Erome: {len(albumes)} álbum(es).")
            return albumes

        titulo = titulo_de(pagina, "Erome")
        videos = videos_de(pagina)
        log_callback(f"[INFO] Title detected: {titulo}")
        if not videos:
            log_callback("[ERROR] El álbum no tiene vídeos (las fotos no se descargan).")
            return None

        log_callback(f"[INFO] {len(videos)} vídeo(s): se guardan como MP4.")
        for i, video in enumerate(videos, 1):
            if check_cancel and check_cancel():
                break
            indice = i if len(videos) > 1 else None
            destino = os.path.join(carpeta_media(), nombre_media(titulo, "mp4", indice))
            log_callback(f"[INFO] Vídeo {i}/{len(videos)}: {os.path.basename(destino)}")
            await descargar_archivo(video, destino, CABECERAS, log_callback, check_cancel)

        return None
