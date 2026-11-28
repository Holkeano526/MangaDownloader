"""
Tubos KVS de leaks (ThotHub, Ultrathots, SheeshFans, ViralXXX, XXBrits).

  Búsqueda: <base>/search/?q=<texto>
  Ficha:    <base>/videos/<id>/<slug>/   o   <base>/video/<id>/<slug>/
  Descarga: `video_alt_url` / `video_url` (MP4 con token)

Son clones de KVS: la ficha trae `video_url: '...'` y, a veces, `video_alt_url` (mejor
calidad). El MP4 exige el Referer de la ficha, así que se descarga con esa cabecera.

Solo se descargan vídeos: las fotos de los listados no se tocan.
"""
import asyncio
import html
import os
import re
from typing import Callable, List, Optional
from urllib.parse import urljoin, urlparse

import aiohttp

from .base import BaseSiteHandler
from .. import config
from ..utils import descargar_archivo, carpeta_media, nombre_media

# id (el que sale en !leak) -> configuración del sitio
TUBOS = [
    dict(id="thothub", re_ficha=r"/videos/\d+/", nombre="ThotHub", base="https://thothub.to",
         buscar="/search/?q={q}", ficha=r"/videos/\d+/[^\"']+/", dominios=["thothub.to"]),
    dict(id="ultrathots", re_ficha=r"/videos/\d+/", nombre="Ultrathots", base="https://ultrathots.com",
         buscar="/search/?q={q}", ficha=r"/videos/\d+/[^\"']+/", dominios=["ultrathots.com"]),
    dict(id="sheeshfans", re_ficha=r"/video/\d+/", nombre="SheeshFans", base="https://sheeshfans.com",
         buscar="/search/?q={q}", ficha=r"/video/\d+/[^\"']+/", dominios=["sheeshfans.com"]),
    dict(id="viralxxxporn", re_ficha=r"/video/\d+/", nombre="ViralXXX", base="https://viralxxxporn.com",
         buscar="/search/?q={q}", ficha=r"/video/\d+/[^\"']+/", dominios=["viralxxxporn.com"]),
    dict(id="xxbrits", re_ficha=r"/videos/\d+/", nombre="XXBrits", base="https://www.xxbrits.com",
         buscar="/search/?q={q}", ficha=r"/videos/\d+/[^\"']+/", dominios=["xxbrits.com"]),
]

_RE_TITULO = re.compile(r"video_title:\s*'((?:[^'\\]|\\.)*)'")
_RE_MP4 = re.compile(r"video_(?:alt_)?url:\s*'([^']+\.mp4[^']*)'", re.I)
_RE_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)
MAX_VIDEOS = 60


def _limpio(texto: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", texto or ""))).strip()


async def _get(s: aiohttp.ClientSession, url: str) -> str:
    for intento in range(2):
        async with s.get(url) as r:
            if r.status == 200:
                return await r.text(errors="replace")
            if r.status in (429, 503) and intento == 0:
                await asyncio.sleep(1.5)
                continue
            return ""
    return ""


def es_ficha(url: str, cfg: dict) -> bool:
    return bool(re.search(cfg["re_ficha"], urlparse(url).path + "/"))


def titulo_de(pagina: str, respaldo: str = "") -> str:
    m = _RE_TITULO.search(pagina)
    if m:
        return _limpio(m.group(1))
    m = _RE_H1.search(pagina)
    if m and _limpio(m.group(1)):
        return _limpio(m.group(1))
    m = re.search(r"<title[^>]*>(.*?)</title>", pagina, re.S | re.I)
    return _limpio(m.group(1)) if m else respaldo


def url_video_de(pagina: str) -> Optional[str]:
    """MP4 directo. `video_alt_url` (más calidad) se prefiere si está."""
    campo_alt = re.search(r"video_alt_url:\s*'([^']+\.mp4[^']*)'", pagina)
    m = campo_alt or _RE_MP4.search(pagina)
    if not m:
        return None
    return html.unescape(m.group(1)).replace("\\/", "/")


def videos_de(pagina: str, base: str, cfg: dict) -> List[str]:
    """Enlaces de ficha de una página de listado (búsqueda, etiqueta, modelo...)."""
    enlaces = re.findall(rf'href="((?:https?://[^"\']+)?{cfg["ficha"]})"', pagina)
    base = base.rstrip("/") + "/"
    return list(dict.fromkeys(urljoin(base, u) for u in enlaces))[:MAX_VIDEOS]


class TuboHandler(BaseSiteHandler):
    """Handler genérico para los tubos KVS (solo vídeos → MP4 en Media/)."""

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def get_supported_domains(self) -> list:
        return self.cfg["dominios"]

    async def process(self, url: str, log_callback: Callable[[str], None], check_cancel: Callable[[], bool],
                      progress_callback: Optional[Callable[[int, int], None]] = None):
        base = self.cfg["base"]
        log_callback(f"[INIT] Procesando {self.cfg.get('nombre', self.cfg['id'])}...")
        cabeceras = {"User-Agent": config.USER_AGENT, "Referer": base + "/"}
        timeout = aiohttp.ClientTimeout(total=40, connect=10)
        async with aiohttp.ClientSession(headers=cabeceras, timeout=timeout) as s:
            pagina = await _get(s, url)
            if not pagina:
                log_callback("[ERROR] El sitio no respondió (vídeo borrado o sitio caído).")
                return None

            if not es_ficha(url, self.cfg):
                # Listado (búsqueda, etiqueta, modelo): devolver los vídeos para encolarlos
                enlaces = videos_de(pagina, base + "/", self.cfg)
                if not enlaces:
                    log_callback("[ERROR] No encontré vídeos en esa página.")
                    return None
                log_callback(f"[INFO] {len(enlaces)} vídeo(s) en la lista.")
                return enlaces

        titulo = titulo_de(pagina, self.cfg.get("nombre", "Vídeo"))
        directo = url_video_de(pagina)
        if not directo:
            log_callback("[ERROR] No encontré la URL del vídeo en la ficha.")
            return None
        destino = os.path.join(carpeta_media(), nombre_media(titulo, "mp4"))
        log_callback(f"[INFO] Title detected: {titulo}")
        log_callback(f"[INFO] Vídeo 1/1: {os.path.basename(destino)}")
        # El CDN de estos tubos exige el Referer de la ficha, no el de la portada
        await descargar_archivo(directo, destino,
                                {"User-Agent": config.USER_AGENT, "Referer": url},
                                log_callback, check_cancel)
        return None
