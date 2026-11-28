"""
NotFans — vídeos de creadoras de OnlyFans (sitio KVS).

  Vídeo:  https://notfans.vip/videos/<id>/<slug>/
  Listas: https://notfans.vip/tags/<etiqueta>/, /models/<nombre>/, /search/<texto>/

La ficha del vídeo trae la URL directa en `video_url:` (y a veces `video_alt_url:` con más
calidad). El vídeo se guarda como MP4 en la carpeta Media.
"""
import html
import os
import re
from typing import Callable, List, Optional
from urllib.parse import urljoin, urlparse

import aiohttp

from .base import BaseSiteHandler
from .. import config
from ..utils import descargar_archivo, carpeta_media, nombre_media

WEB = "https://notfans.vip"  # notfans.com es el mismo sitio con otro dominio
DOMINIOS = ["notfans.vip", "notfans.com"]
CABECERAS = {"User-Agent": config.USER_AGENT, "Referer": f"{WEB}/"}
MAX_VIDEOS = 60
_RE_VIDEO = re.compile(r"/videos/(\d+)/")
_RE_TITULO = re.compile(r"video_title:\s*'((?:[^'\\]|\\.)*)'")


def _limpio(texto: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", texto or ""))).strip()


async def _get(s: aiohttp.ClientSession, url: str) -> str:
    async with s.get(url) as r:
        if r.status != 200:
            return ""
        return await r.text(errors="replace")


def titulo_de(pagina: str, respaldo: str = "") -> str:
    m = _RE_TITULO.search(pagina)
    if m:
        return _limpio(m.group(1))
    m = re.search(r"<title[^>]*>(.*?)</title>", pagina, re.S | re.I)
    return _limpio(m.group(1)) if m else respaldo


def url_video_de(pagina: str) -> Optional[str]:
    """URL MP4 directa. Si hay `video_alt_url` (más calidad) se prefiere."""
    for campo in ("video_alt_url", "video_url"):
        m = re.search(rf"{campo}:\s*'([^']+\.mp4[^']*)'", pagina)
        if m:
            return html.unescape(m.group(1)).replace("\\/", "/")
    return None


def videos_de(pagina: str, base: str = WEB) -> List[str]:
    """Enlaces de vídeo de una página de listado (etiqueta, modelo, búsqueda...)."""
    enlaces = [urljoin(base + "/", u) for u in re.findall(r'href="([^"]*/videos/\d+/[^"]*/)"', pagina)]
    return list(dict.fromkeys(enlaces))[:MAX_VIDEOS]


class NotFansHandler(BaseSiteHandler):
    """Handler para notfans.vip / notfans.com (vídeos → MP4)."""

    @staticmethod
    def get_supported_domains() -> list:
        return DOMINIOS

    async def process(self, url: str, log_callback: Callable[[str], None], check_cancel: Callable[[], bool],
                      progress_callback: Optional[Callable[[int, int], None]] = None):
        log_callback("[INIT] Procesando NotFans...")
        base = "{0.scheme}://{0.netloc}".format(urlparse(url))
        cabeceras = {"User-Agent": config.USER_AGENT, "Referer": base + "/"}
        timeout = aiohttp.ClientTimeout(total=40, connect=10)
        async with aiohttp.ClientSession(headers=cabeceras, timeout=timeout) as s:
            pagina = await _get(s, url)
        if not pagina:
            log_callback("[ERROR] NotFans no respondió (vídeo borrado o sitio caído).")
            return None

        if not _RE_VIDEO.search(url):
            # Etiqueta, modelo, búsqueda: devolver los vídeos para encolarlos
            enlaces = videos_de(pagina, base)
            if not enlaces:
                log_callback("[ERROR] No encontré vídeos en esa página de NotFans.")
                return None
            log_callback(f"[INFO] Lista de NotFans: {len(enlaces)} vídeo(s).")
            return enlaces

        titulo = titulo_de(pagina, "NotFans")
        video = url_video_de(pagina)
        log_callback(f"[INFO] Title detected: {titulo}")
        if not video:
            log_callback("[ERROR] No encontré la URL del vídeo en la ficha.")
            return None
        destino = os.path.join(carpeta_media(), nombre_media(titulo, "mp4"))
        log_callback(f"[INFO] Vídeo 1/1: {os.path.basename(destino)}")
        await descargar_archivo(video, destino,
                                {"User-Agent": config.USER_AGENT, "Referer": url},
                                log_callback, check_cancel)
        return None
