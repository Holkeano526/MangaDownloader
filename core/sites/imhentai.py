"""
IMHentai — descarga por HTTP, sin navegador.

  Galería: https://imhentai.xxx/gallery/<id>/      (también /view/<id>/<página>/)
  Artista: https://imhentai.xxx/artist/<nombre>/   (lista de galerías)

La ficha trae todo lo necesario en campos ocultos y en un JSON:
  <input id="load_server" value="10">  <input id="load_dir" value="031">
  <input id="load_id" value="h8n6uqy02z">  <input id="load_pages" value="14">
  g_th = $.parseJSON('{"1":"w,1280,1895", ...}')   -> extensión de cada página
Página n: https://m<server>.imhentai.xxx/<dir>/<load_id>/<n>.<ext>
Extensiones: j=jpg, p=png, w=webp, g=gif, b=bmp.
"""
import html
import json
import re
from typing import Callable, List, Optional
from urllib.parse import urljoin

import aiohttp

from .base import BaseSiteHandler
from .. import config
from ..http import crear_sesion
from ..utils import clean_filename, download_and_make_pdf

WEB = "https://imhentai.xxx"
CABECERAS = {"User-Agent": config.USER_AGENT, "Referer": f"{WEB}/"}
EXT = {"j": "jpg", "p": "png", "w": "webp", "g": "gif", "b": "bmp"}
MAX_GALERIAS_LISTA = 60


def _campo(pagina: str, nombre: str) -> Optional[str]:
    m = re.search(rf'id="{nombre}"\s+value="([^"]*)"', pagina)
    return m.group(1) if m else None


def paginas_de_galeria(pagina: str) -> List[str]:
    servidor, carpeta, clave, total = (_campo(pagina, n) for n in ("load_server", "load_dir", "load_id", "load_pages"))
    if not (servidor and carpeta and clave and total and total.isdigit()):
        return []
    extensiones = {}
    m = re.search(r"g_th\s*=\s*\$\.parseJSON\('(.*?)'\)", pagina, re.S)
    if m:
        try:
            extensiones = json.loads(m.group(1))
        except ValueError:
            extensiones = {}
    urls = []
    for n in range(1, int(total) + 1):
        letra = str(extensiones.get(str(n), "j")).split(",")[0]
        urls.append(f"https://m{servidor}.imhentai.xxx/{carpeta}/{clave}/{n}.{EXT.get(letra, 'jpg')}")
    return urls


class IMHentaiHandler(BaseSiteHandler):
    """Handler para imhentai.xxx."""

    @staticmethod
    def get_supported_domains() -> list:
        return ["imhentai.xxx"]

    async def process(self, url: str, log_callback: Callable[[str], None], check_cancel: Callable[[], bool],
                      progress_callback: Optional[Callable[[int, int], None]] = None):
        m = re.search(r"/(?:gallery|view)/(\d+)", url)
        destino = f"{WEB}/gallery/{m.group(1)}/" if m else url
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        async with crear_sesion(headers=CABECERAS, timeout=timeout) as s:
            async with s.get(destino) as r:
                pagina = await r.text(errors="replace") if r.status == 200 else ""
                estado = r.status
        if not pagina:
            log_callback(f"[ERROR] IMHentai respondió {estado}.")
            return None

        if not m:
            # Artista, etiqueta, búsqueda...: devolver las galerías para encolarlas
            enlaces = []
            for g in re.findall(r'href="(/gallery/\d+/)"', pagina):
                u = urljoin(WEB, g)
                if u not in enlaces:
                    enlaces.append(u)
            if not enlaces:
                log_callback("[ERROR] No encontré galerías en esa página de IMHentai.")
                return None
            log_callback(f"[INFO] Lista de IMHentai: {len(enlaces[:MAX_GALERIAS_LISTA])} galerías.")
            return enlaces[:MAX_GALERIAS_LISTA]

        t = re.search(r"<h1[^>]*>(.*?)</h1>", pagina, re.S)
        titulo = html.unescape(re.sub(r"<[^>]+>", "", t.group(1))).strip() if t else f"IMHentai {m.group(1)}"
        urls = paginas_de_galeria(pagina)
        log_callback(f"[INFO] Title detected: {titulo}")
        if not urls:
            log_callback("[ERROR] IMHentai cambió el formato de la ficha: no encuentro las páginas.")
            return None
        log_callback(f"[INFO] Images detected: {len(urls)}")
        await download_and_make_pdf(urls, f"{clean_filename(titulo)}.pdf", CABECERAS, log_callback, check_cancel,
                                    progress_callback=progress_callback, open_result=config.OPEN_RESULT_ON_FINISH)
        return None
