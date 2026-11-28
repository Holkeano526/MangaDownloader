"""
WordPress de leaks con MP4 propio: DirtyShip y GotAnyNudes.

  Búsqueda: <base>/?s=<texto>   (tarjetas <article> con el enlace al post)
  Post:     <base>/<slug>/   o   <base>/leak/<modelo>/<slug>/
  Vídeo:    <source src="https://cdn*.dirtyship.net/...mp4">
            o `https://ares.gotanynudes.com/feed/<modelo>/<archivo>.mp4`

Solo se bajan los posts con vídeo; las galerías de fotos (`/gallery/`) se omiten.
"""
import html
import os
import re
from typing import Callable, List, Optional, Tuple
from urllib.parse import urlparse

import aiohttp

from .base import BaseSiteHandler
from .. import config
from ..utils import descargar_archivo, carpeta_media, nombre_media

WORDPRESS = [
    dict(id="dirtyship", nombre="DirtyShip", base="https://dirtyship.com", dominios=["dirtyship.com"],
         re_post=r"https://dirtyship\.com/[a-z0-9][a-z0-9-]*/",
         malas=r"^(?:gallery|performer|category|tag|actor|actors|feed|page|wp-|xmlrpc)", tipo="dirtyship"),
    dict(id="gotanynudes", nombre="GotAnyNudes", base="https://gotanynudes.com", dominios=["gotanynudes.com"],
         re_post=r"https://gotanynudes\.com/(?:leak/[a-z0-9-]+/)?[a-z0-9][a-z0-9-]*/",
         malas=r"^(?:tag|category|author|feed|page|wp-|xmlrpc|community|spotlight|goonmon|tcg)", tipo="gotanynudes"),
]

_RE_VIDEO = re.compile(r'src=["\']?(https?://[^"\'\s>]+\.mp4[^"\'\s>]*)', re.I)
_RE_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)
_RE_OG_TITULO = re.compile(r'property="og:title"[^>]+content="([^"]+)"', re.I)
MAX_POSTS = 60


def _limpio(texto: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", texto or ""))).strip()


async def _get(s: aiohttp.ClientSession, url: str) -> str:
    async with s.get(url) as r:
        if r.status != 200:
            return ""
        return await r.text(errors="replace")


def es_post(url: str, cfg: dict) -> bool:
    return bool(re.search(cfg["re_post"], url)) and not es_galeria(url, cfg)


def es_galeria(url: str, cfg: dict) -> bool:
    partes = [p for p in urlparse(url).path.split("/") if p]
    return bool(partes and (partes[0] == "gallery" or re.search(cfg["malas"], partes[0])))


def titulo_de(pagina: str, respaldo: str = "") -> str:
    for patron in (_RE_H1, _RE_OG_TITULO):
        m = patron.search(pagina)
        if m and _limpio(m.group(1)):
            return _limpio(m.group(1))
    m = re.search(r"<title[^>]*>(.*?)</title>", pagina, re.S | re.I)
    return _limpio(m.group(1)) if m else respaldo


def video_de(pagina: str) -> Optional[str]:
    """Primer MP4 propio del post (DirtyShip usa `src=` sin comillas). Las calidades
    repetidas apuntan al mismo archivo; se queda la primera (la más alta)."""
    for bruto in _RE_VIDEO.findall(pagina):
        url = bruto.replace("\\/", "/").rstrip("\\").strip()
        if url.startswith("http"):
            return url
    return None


def posts_de(pagina: str, cfg: dict) -> List[Tuple[str, str, str]]:
    """[(url, título, portada)] de una página de resultados de WordPress."""
    vistos: dict = {}
    for m in re.finditer(rf'<a\b([^>]*href="({cfg["re_post"]})"[^>]*)>(.*?)</a>', pagina, re.S | re.I):
        attrs, url, dentro = m.group(1), m.group(2), m.group(3)
        if es_galeria(url, cfg):
            continue
        titulo = ""
        for patron in (r'\btitle="([^"]+)"', r'\balt="([^"]+)"', r'aria-label="([^"]+)"'):
            if t := re.search(patron, attrs):
                titulo = _limpio(t.group(1))
                break
        if not titulo:
            texto = _limpio(dentro)
            if len(texto) >= 4:
                titulo = texto
        img = re.search(r'(?:data-src|src)="(https?://[^"]+)"', dentro)
        if titulo:
            previo = vistos.get(url)
            if previo is None or len(titulo) > len(previo[0]):
                vistos[url] = (titulo, img.group(1) if img else "")
    return [(u, t, i) for u, (t, i) in list(vistos.items())[:MAX_POSTS]]


class WPVideoHandler(BaseSiteHandler):
    """Handler genérico para los WordPress con MP4 propio (solo vídeos)."""

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def get_supported_domains(self) -> list:
        return self.cfg["dominios"]

    async def process(self, url: str, log_callback: Callable[[str], None], check_cancel: Callable[[], bool],
                      progress_callback: Optional[Callable[[int, int], None]] = None):
        log_callback(f"[INIT] Procesando {self.cfg.get('nombre', self.cfg['id'])}...")
        timeout = aiohttp.ClientTimeout(total=40, connect=10)
        cabeceras = {"User-Agent": config.USER_AGENT, "Referer": self.cfg["base"] + "/"}
        async with aiohttp.ClientSession(headers=cabeceras, timeout=timeout) as s:
            pagina = await _get(s, url)
        if not pagina:
            log_callback("[ERROR] El sitio no respondió (post borrado o sitio caído).")
            return None

        if es_galeria(url, self.cfg) or not es_post(url, self.cfg):
            log_callback("[ERROR] Esa página no es un post de vídeo (solo se descargan vídeos).")
            return None

        titulo = titulo_de(pagina, self.cfg.get("nombre", "Vídeo"))
        directo = video_de(pagina)
        if not directo:
            log_callback("[ERROR] El post no tiene un vídeo propio descargable.")
            return None
        destino = os.path.join(carpeta_media(), nombre_media(titulo, "mp4"))
        log_callback(f"[INFO] Title detected: {titulo}")
        log_callback(f"[INFO] Vídeo 1/1: {os.path.basename(destino)}")
        await descargar_archivo(directo, destino,
                                {"User-Agent": config.USER_AGENT, "Referer": url},
                                log_callback, check_cancel)
        return None
