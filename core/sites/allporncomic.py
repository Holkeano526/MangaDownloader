"""
AllPornComic — WordPress con el tema Madara (WP-Manga). Descarga por HTTP, sin navegador.

  Capítulo: https://allporncomic.com/porncomic/<cómic>/<capítulo>/
  Cómic   : https://allporncomic.com/porncomic/<cómic>/          (lista de capítulos)
  Artista : https://allporncomic.com/porncomicartist/<artista>/  (lista de cómics)

Capítulo: <img id="image-N" data-src=" https://cdn.allporncomic.com/.../001.jpg" class="wp-manga-chapter-img">
(ojo: la URL lleva un espacio delante). Capítulos de un cómic: POST <cómic>/ajax/chapters/ (Madara).
Un cómic o un artista devuelven la lista de capítulos para que el bot los encole.
"""
import html
import re
from typing import Callable, List, Optional
from urllib.parse import urlparse

import aiohttp

from .base import BaseSiteHandler
from .. import config
from ..utils import clean_filename, download_and_make_pdf

WEB = "https://allporncomic.com"
CABECERAS = {"User-Agent": config.USER_AGENT, "Referer": f"{WEB}/"}
MAX_CAPITULOS_LISTA = 60


def imagenes_del_capitulo(pagina: str) -> List[str]:
    urls = []
    for img in re.findall(r"<img[^>]*wp-manga-chapter-img[^>]*>", pagina):
        m = re.search(r'(?:data-src|src)="\s*([^"]+?)\s*"', img)
        if m and m.group(1) not in urls:
            urls.append(m.group(1))
    return urls


def _segmentos(url: str) -> List[str]:
    return [p for p in urlparse(url).path.split("/") if p]


class AllPornComicHandler(BaseSiteHandler):
    """Handler para allporncomic.com."""

    @staticmethod
    def get_supported_domains() -> list:
        return ["allporncomic.com"]

    async def process(self, url: str, log_callback: Callable[[str], None], check_cancel: Callable[[], bool],
                      progress_callback: Optional[Callable[[int, int], None]] = None):
        partes = _segmentos(url)
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        async with aiohttp.ClientSession(headers=CABECERAS, timeout=timeout) as s:
            if len(partes) >= 3 and partes[0] == "porncomic":
                return await self._capitulo(s, url, log_callback, check_cancel, progress_callback)
            if len(partes) == 2 and partes[0] == "porncomic":
                return await self._capitulos_del_comic(s, f"{WEB}/porncomic/{partes[1]}/", log_callback)
            if len(partes) >= 2 and partes[0] in ("porncomicartist", "porncomic-genre", "porncomic-tag"):
                return await self._comics_de_lista(s, url, log_callback)
        log_callback("[ERROR] Enlace de AllPornComic no reconocido (usa un capítulo, un cómic o un artista).")
        return None

    async def _capitulo(self, s, url, log_callback, check_cancel, progress_callback):
        async with s.get(url) as r:
            pagina = await r.text(errors="replace") if r.status == 200 else ""
            estado = r.status
        if not pagina:
            log_callback(f"[ERROR] AllPornComic respondió {estado}.")
            return None
        m = re.search(r"<title>(.*?)</title>", pagina, re.S)
        titulo = html.unescape(m.group(1)).replace(" - AllPornComic", "").strip() if m else "AllPornComic"
        # "Cómic [Artista] - 1 . Capítulo - Chapter 1 (Serie) [Artista]" -> se queda la parte del capítulo
        if " - " in titulo:
            cab, resto = titulo.split(" - ", 1)
            capitulo = re.search(r"Chapter\s*\d+(?:\.\d+)?", resto)
            if capitulo:  # "Cómic (Serie) [Artista] - Chapter 1"
                titulo = f"{cab} - {capitulo.group(0)}"
        urls = imagenes_del_capitulo(pagina)
        log_callback(f"[INFO] Title detected: {titulo}")
        if not urls:
            log_callback("[ERROR] No encontré las páginas del capítulo en AllPornComic.")
            return None
        log_callback(f"[INFO] Images detected: {len(urls)}")
        await download_and_make_pdf(urls, f"{clean_filename(titulo)[:180]}.pdf", CABECERAS, log_callback,
                                    check_cancel, progress_callback=progress_callback,
                                    open_result=config.OPEN_RESULT_ON_FINISH)
        return None

    async def _capitulos_del_comic(self, s, comic_url, log_callback):
        async with s.post(comic_url + "ajax/chapters/") as r:
            bloque = await r.text(errors="replace") if r.status == 200 else ""
        prefijo = re.escape(comic_url)
        caps = []
        for u in re.findall(rf'href="({prefijo}[^"/]+/)"', bloque):
            if u not in caps:
                caps.append(u)
        if not caps:
            log_callback("[ERROR] No encontré capítulos en ese cómic de AllPornComic.")
            return None
        caps.reverse()  # Madara los lista del más nuevo al más viejo
        log_callback(f"[INFO] AllPornComic: {len(caps)} capítulo(s).")
        return caps[:MAX_CAPITULOS_LISTA]

    async def _comics_de_lista(self, s, url, log_callback):
        async with s.get(url) as r:
            pagina = await r.text(errors="replace") if r.status == 200 else ""
        # La lista de un artista enlaza cada cómic (y solo sus últimos capítulos): se sacan los
        # cómics y luego todos sus capítulos con el AJAX de Madara.
        comics = []
        for slug in re.findall(rf'href="{re.escape(WEB)}/porncomic/([^"/?]+)/"', pagina):
            if slug not in comics:
                comics.append(slug)
        enlaces = []
        for slug in comics:
            caps = await self._capitulos_del_comic(s, f"{WEB}/porncomic/{slug}/", lambda _m: None) or []
            enlaces += [c for c in caps if c not in enlaces]
            if len(enlaces) >= MAX_CAPITULOS_LISTA:
                break
        if not enlaces:
            log_callback("[ERROR] No encontré cómics en esa página de AllPornComic.")
            return None
        log_callback(f"[INFO] AllPornComic: {len(comics)} cómic(s), {len(enlaces[:MAX_CAPITULOS_LISTA])} capítulo(s).")
        return enlaces[:MAX_CAPITULOS_LISTA]
