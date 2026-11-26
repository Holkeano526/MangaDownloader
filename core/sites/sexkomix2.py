"""
SexKomix2 — descarga por HTTP, sin navegador.

  Cómic  : https://sexkomix2.com/comicsx_<idioma>/<id>-<slug>/   (idioma: en, es, ru, pt, de...)
  Estudio: https://sexkomix2.com/studios/?lang=en&cat=<artista>   (lista de cómics, paginada)
  Imágenes: <ul id="comix_pages_ul"> con <a rel="group" class="fancybox" href="<imagen completa>">.
            El primer <li> es la portada (data-src). Las tarjetas de "Similar comics" van después de
            <div id="more_comics_directory...">, así que se corta ahí.

El HTML usa espacios alrededor del "=" (href = "..."), de ahí los \\s* en las expresiones.
Una página de estudio o de categoría devuelve la lista de cómics: el bot los pone en cola.
"""
import html
import re
from typing import Callable, List, Optional
from urllib.parse import urljoin

import aiohttp

from .base import BaseSiteHandler
from .. import config
from ..http import crear_sesion
from ..utils import clean_filename, download_and_make_pdf

WEB = "https://sexkomix2.com"
CABECERAS = {"User-Agent": config.USER_AGENT, "Referer": f"{WEB}/"}
COMIC = re.compile(r"/comicsx_[a-z]{2}/\d+-[^\"'\s/]+/?")
MAX_COMICS_LISTA = 60  # una página de estudio no debería encolar cientos de descargas


def imagenes_del_comic(pagina: str) -> List[str]:
    """Portada + páginas en orden, sin repetir y sin las tarjetas de cómics similares."""
    i = pagina.find("comix_pages_ul")
    if i < 0:
        return []
    j = pagina.find("more_comics_directory", i)
    bloque = pagina[i:j if j > 0 else len(pagina)]
    urls: List[str] = []
    portada = re.search(r'<li>\s*<img[^>]*data-src\s*=\s*"([^"]+)"', bloque)
    if portada:
        urls.append(portada.group(1).strip())
    for u in re.findall(r'rel\s*=\s*"group"[^>]*href\s*=\s*"([^"]+)"', bloque):
        u = u.strip()
        if u not in urls:
            urls.append(u)
    return [urljoin(WEB, u) for u in urls]


def titulo_del_comic(pagina: str) -> str:
    m = re.search(r"<title>(.*?)</title>", pagina, re.S)
    t = html.unescape(m.group(1)) if m else "SexKomix2"
    t = re.sub(r"^[^\w\[(]+", "", t)                      # emoji del principio
    t = t.split("|")[0]
    t = re.sub(r"^(?:Porn comic|Порно комикс|Cómic porno|Comic porno|Quadrinho pornô)\s+", "", t, flags=re.I)
    t = re.sub(r"\s+(?:Sex comic|Секс комикс|Cómic sexual|Comic sexual)\b.*$", "", t, flags=re.I)
    return t.strip(" .") or "SexKomix2"


class SexKomix2Handler(BaseSiteHandler):
    """Handler para sexkomix2.com."""

    @staticmethod
    def get_supported_domains() -> list:
        return ["sexkomix2.com"]

    async def process(self, url: str, log_callback: Callable[[str], None], check_cancel: Callable[[], bool],
                      progress_callback: Optional[Callable[[int, int], None]] = None):
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        async with crear_sesion(headers=CABECERAS, timeout=timeout) as s:
            async with s.get(url) as r:
                pagina = await r.text(errors="replace") if r.status == 200 else ""
                estado = r.status
        if not pagina:
            log_callback(f"[ERROR] SexKomix2 respondió {estado}.")
            return None
        if "Prohibido el acceso" in pagina or "Access to this page is prohibited" in pagina:
            log_callback("[ERROR] SexKomix2 no ofrece este cómic en ese idioma. Prueba con el enlace en inglés "
                         "(comicsx_en).")
            return None

        if not COMIC.search(url):
            # Estudio, categoría o búsqueda: devolver los cómics de la lista para encolarlos
            enlaces = []
            for m in re.finditer(r'<li class\s*=\s*"comix">\s*<a href\s*=\s*"([^"]+)"', pagina):
                u = urljoin(WEB, m.group(1).strip())
                if u not in enlaces:
                    enlaces.append(u)
            if not enlaces:
                log_callback("[ERROR] No encontré cómics en esa página de SexKomix2.")
                return None
            if len(enlaces) > MAX_COMICS_LISTA:
                log_callback(f"[WARN] La página tiene {len(enlaces)} cómics; se encolan los {MAX_COMICS_LISTA} primeros.")
            log_callback(f"[INFO] Lista de SexKomix2: {len(enlaces[:MAX_COMICS_LISTA])} cómics.")
            return enlaces[:MAX_COMICS_LISTA]

        titulo = titulo_del_comic(pagina)
        urls = imagenes_del_comic(pagina)
        log_callback(f"[INFO] Title detected: {titulo}")
        if not urls:
            log_callback("[ERROR] No encontré las páginas del cómic en SexKomix2.")
            return None
        log_callback(f"[INFO] Images detected: {len(urls)}")
        await download_and_make_pdf(urls, f"{clean_filename(titulo)}.pdf", CABECERAS, log_callback, check_cancel,
                                    progress_callback=progress_callback, open_result=config.OPEN_RESULT_ON_FINISH)
        return None
