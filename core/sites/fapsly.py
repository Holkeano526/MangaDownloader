"""
Fapsly — posts de OnlyFans de creadoras (solo vídeos).

  Post:     https://fapsly.net/onlyfans/<usuaria>/<slug>/
  Creadora: https://fapsly.net/<usuaria>/   (lista de posts)

El post trae las URLs completas en `original="..."` (y `_watermarked.mp4` para vídeos):
se descargan los vídeos (MP4 en Media) y las fotos se ignoran.

El sitio responde 503 con una página «Loading…» mientras entrega una cookie de sesión
(`pxc`) desde `/icons/loading_new.gif`. Se pide ese gif una vez, como haría el navegador,
y se reintenta; no se resuelve ningún captcha ni desafío.
"""
import asyncio
import html
import os
import re
from typing import Callable, List, Optional, Tuple
from urllib.parse import urljoin

import aiohttp

from .base import BaseSiteHandler
from .. import config
from ..utils import descargar_archivo, carpeta_media, nombre_media

WEB = "https://fapsly.net"
CABECERAS = {"User-Agent": config.USER_AGENT, "Referer": f"{WEB}/"}
MAX_POSTS = 60
_RE_POST = re.compile(r"/onlyfans/([^/?#]+)/([^/?#]+)/?")
_RE_ORIGINAL = re.compile(r'\boriginal="(https://[^"]+)"')
_RE_WATERMARKED = re.compile(r'https://[^"\'<>\s]+_watermarked\.(?:jpe?g|png|webp|mp4)')
_RE_TITULO = re.compile(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', re.I)


def _limpio(texto: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", texto or ""))).strip()


async def _pedir(s: aiohttp.ClientSession, url: str) -> str:
    if not len(s.cookie_jar):
        # El sitio entrega su cookie de sesión (`pxc`) junto a ese gif; sin ella responde 503
        # con una página «Loading…». Se pide una vez, como el navegador, y no se resuelve
        # ningún captcha ni desafío.
        async with s.get(f"{WEB}/icons/loading_new.gif?g=1") as g:
            await g.read()
    for intento in range(3):
        async with s.get(url) as r:
            if r.status == 200:
                return await r.text(errors="replace")
            if r.status == 503:
                # Cookie caducada o sesión nueva: se vuelve a pedir y se reintenta.
                async with s.get(f"{WEB}/icons/loading_new.gif?g=1") as g:
                    await g.read()
                await asyncio.sleep(1.0 + intento)
                continue
            return ""
    return ""


def titulo_de(pagina: str, respaldo: str = "") -> str:
    m = _RE_TITULO.search(pagina)
    if m:
        return _limpio(m.group(1))
    m = re.search(r"<title[^>]*>(.*?)</title>", pagina, re.S | re.I)
    return _limpio(m.group(1)) if m else respaldo


def es_post(url: str) -> bool:
    return bool(_RE_POST.search(url))


def media_de(pagina: str) -> Tuple[List[str], List[str]]:
    """(fotos, vídeos) de la galería del post, en el orden de la página."""
    urls = [urljoin(WEB + "/", u) for u in _RE_ORIGINAL.findall(pagina)]
    if not urls:
        urls = _RE_WATERMARKED.findall(pagina)
    fotos = [u for u in dict.fromkeys(urls) if re.search(r"\.(?:jpe?g|png|webp)(?:\?|$)", u, re.I)]
    videos = [u for u in dict.fromkeys(urls) if re.search(r"\.mp4(?:\?|$)", u, re.I)]
    return fotos, videos


def posts_de(pagina: str) -> List[str]:
    """Posts de una página de creadora (o de cualquier listado)."""
    enlaces = [urljoin(WEB + "/", u) for u in
               re.findall(r'href="(/onlyfans/[^/"]+/[^/"]+/)"', pagina)]
    return list(dict.fromkeys(enlaces))[:MAX_POSTS]


class FapslyHandler(BaseSiteHandler):
    """Handler para fapsly.net (fotos → PDF, vídeos → MP4)."""

    @staticmethod
    def get_supported_domains() -> list:
        return ["fapsly.net"]

    async def process(self, url: str, log_callback: Callable[[str], None], check_cancel: Callable[[], bool],
                      progress_callback: Optional[Callable[[int, int], None]] = None):
        log_callback("[INIT] Procesando Fapsly...")
        timeout = aiohttp.ClientTimeout(total=40, connect=10)
        async with aiohttp.ClientSession(headers=CABECERAS, timeout=timeout,
                                         cookie_jar=aiohttp.CookieJar()) as s:
            pagina = await _pedir(s, url)
        if not pagina:
            log_callback("[ERROR] Fapsly no respondió (post borrado o sitio caído).")
            return None

        if not es_post(url):
            # Creadora o listado: devolver los posts para encolarlos
            posts = posts_de(pagina)
            if not posts:
                log_callback("[ERROR] No encontré posts en esa página de Fapsly.")
                return None
            log_callback(f"[INFO] Creadora de Fapsly: {len(posts)} post(s).")
            return posts

        titulo = titulo_de(pagina, "Fapsly")
        _fotos, videos = media_de(pagina)
        log_callback(f"[INFO] Title detected: {titulo}")
        if not videos:
            log_callback("[ERROR] El post no tiene vídeos (las fotos no se descargan).")
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
