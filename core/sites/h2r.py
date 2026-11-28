"""
Hentai2Read — descarga por HTTP (rehecho el 25/11/2026).

El sitio pasó a ser una app Inertia.js: los datos de cada página van en JSON dentro de
<script data-page="app" type="application/json">{"component": ..., "props": {...}}</script>.
El handler antiguo buscaba "var gData" del sitio anterior y ya no encontraba nada.

  Ficha  : /<tipo>/<id>-<slug>             props.book (título, grupos: artista...), props.chapters
  Lector : /reader/<id>-<slug>/<cap>/page-1 props.images = [URL de cada página] (hentaicdn.com)
  Buscar : /search?q=<texto>               props.books.data (título, url, portada, artista)

Si la ficha tiene varios capítulos, se devuelve la lista de URLs de lector y el bot los encola
uno a uno (igual que las series de otros sitios). Con un solo capítulo se descarga directamente.
"""
import json
import re
from typing import Callable, List, Optional

import aiohttp

from .base import BaseSiteHandler
from .. import config
from ..utils import clean_filename, download_and_make_pdf

WEB = "https://hentai2read.com"


def datos_pagina(html_txt: str) -> dict:
    """El JSON de Inertia de una página de Hentai2Read ({} si no está)."""
    m = re.search(r'<script[^>]*data-page="app"[^>]*>(.*?)</script>', html_txt, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except ValueError:
        return {}


class H2RHandler(BaseSiteHandler):
    """Handler para hentai2read.com."""

    @staticmethod
    def get_supported_domains() -> list:
        return ["hentai2read"]

    async def process(
        self,
        url: str,
        log_callback: Callable[[str], None],
        check_cancel: Callable[[], bool],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Optional[List[str]]:
        log_callback("[INIT] Processing Hentai2Read...")
        headers = {"User-Agent": config.USER_AGENT, "Referer": f"{WEB}/"}
        async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as s:
            async with s.get(url, allow_redirects=True) as r:
                if r.status != 200:
                    log_callback(f"[ERROR] Hentai2Read respondió {r.status}.")
                    return None
                pagina = await r.text()
        datos = datos_pagina(pagina)
        props = datos.get("props") or {}
        componente = datos.get("component", "")

        if componente.startswith("Book"):  # ficha
            caps = props.get("chapters") or []
            urls = [f"{WEB}{c['url']}" for c in caps if c.get("url")]
            if not urls and (props.get("read") or {}).get("url"):
                urls = [f"{WEB}{props['read']['url']}"]
            if len(urls) > 1:
                log_callback(f"[INFO] Serie con {len(urls)} capítulos.")
                urls = list(reversed(urls))  # el sitio los lista del más nuevo al más antiguo
                if len(urls) > config.MAX_CAPITULOS_SERIE:
                    log_callback(f"[WARN] Se encolan los {config.MAX_CAPITULOS_SERIE} primeros capítulos "
                                 f"de {len(urls)}.")
                    urls = urls[:config.MAX_CAPITULOS_SERIE]
                return urls
            if not urls:
                log_callback("[ERROR] La ficha de Hentai2Read no tiene capítulos.")
                return None
            return await self.process(urls[0], log_callback, check_cancel, progress_callback)

        imagenes = props.get("images") or []
        if not imagenes:
            log_callback("[ERROR] No encontré imágenes en el lector de Hentai2Read (¿formato nuevo?).")
            return None
        libro = props.get("book") or {}
        capitulo = props.get("chapter") or {}
        titulo = capitulo.get("title") or libro.get("title") or "hentai2read"
        log_callback(f"[INFO] Title detected: {titulo}")
        log_callback(f"[INFO] Images detected: {len(imagenes)}")
        await download_and_make_pdf(
            imagenes,
            f"{clean_filename(titulo)}.pdf",
            headers,
            log_callback,
            check_cancel,
            progress_callback=progress_callback,
            open_result=config.OPEN_RESULT_ON_FINISH,
        )
        return None
