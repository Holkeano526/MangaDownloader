"""
Bunkr — álbumes de archivos (fotos y vídeos), muy usados para packs de OnlyFans.

  Álbum: https://bunkr.cr/a/<id>
  Ficha: https://bunkr.cr/f/<id>

La página del álbum lista los archivos con su nombre (`title="..."`), su tamaño y su enlace
`/f/<id>`. Para descargar, la ficha del archivo da un `data-file-id`; la API pública del sitio
(`POST /api/_001_v2`) devuelve la ruta del CDN y `glb-apisign.cdn.cr` firma la URL. Es el
mismo flujo que hace el navegador al pulsar «Download», sin resolver ningún captcha.

Solo se descargan vídeos (MP4 en la carpeta Media); las fotos del álbum se ignoran.
`listar_videos()` y `descargar_videos()` las usa el bot para enseñar el menú de un álbum.
"""
import asyncio
import html
import os
import re
from typing import Callable, List, Optional, Tuple
from urllib.parse import unquote, urljoin, urlparse

import aiohttp

from .base import BaseSiteHandler
from .. import config
from ..utils import descargar_archivo, carpeta_media, nombre_media

WEB = "https://bunkr.cr"
DOMINIOS = ["bunkr.cr", "bunkr.si", "bunkr.ru", "bunkr.su", "bunkr.site", "bunkr.media",
            "bunkr.ws", "bunkr.fi", "bunkr.ph", "bunkr.black", "bunkr.red", "bunkr.nexus",
            "bunkr.art", "bunkr.sk", "bunkrsearch.com"]
CABECERAS = {"User-Agent": config.USER_AGENT, "Referer": f"{WEB}/"}
MAX_ARCHIVOS = 200
FIRMA = "https://glb-apisign.cdn.cr/sign"
_EXT_VIDEO = {"mp4", "m4v", "mov", "webm", "mkv"}
_RE_ALBUM = re.compile(r"/a/([A-Za-z0-9]+)")
_RE_FICHA = re.compile(r"/f/([A-Za-z0-9]+)")
_RE_TITULO = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)


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


def titulo_de(pagina: str, respaldo: str = "") -> str:
    m = _RE_TITULO.search(pagina)
    if m and _limpio(m.group(1)):
        return _limpio(m.group(1))
    m = re.search(r"<title[^>]*>(.*?)</title>", pagina, re.S | re.I)
    return _limpio(m.group(1)) if m else respaldo


def _items_de(pagina: str) -> List[Tuple[str, str, str]]:
    """[(nombre, url de la ficha /f/<id>, tamaño)] del álbum, sin los «archivos relacionados»."""
    corte = re.search(r"<h2[^>]*files-album", pagina, re.I)
    if corte:
        pagina = pagina[:corte.start()]
    items = []
    for trozo in pagina.split('class="relative group/item theItem"')[1:]:
        nombre = re.search(r'title="([^"]+)"', trozo)
        enlace = re.search(r'href="(/f/[A-Za-z0-9]+)"', trozo)
        tamano = re.search(r"\btheSize[^>]*>([^<]+)<", trozo)
        if nombre and enlace:
            items.append((html.unescape(nombre.group(1)).strip(),
                          urljoin(WEB + "/", enlace.group(1)),
                          _limpio(tamano.group(1)) if tamano else ""))
    return items[:MAX_ARCHIVOS]


def archivos_de(pagina: str) -> List[Tuple[str, str]]:
    """[(nombre, url de la ficha /f/<id>)] del álbum (todas las extensiones)."""
    return [(n, u) for n, u, _ in _items_de(pagina)]


def videos_del_album(pagina: str) -> List[Tuple[str, str, str]]:
    """[(nombre, ficha, tamaño)] solo de los vídeos del álbum."""
    return [(n, u, t) for n, u, t in _items_de(pagina) if extension_de(n) in _EXT_VIDEO]


def extension_de(nombre: str) -> str:
    return os.path.splitext(nombre.rsplit("/", 1)[-1])[1].lstrip(".").lower()


def nombre_de_ficha(pagina: str) -> str:
    """Nombre real del archivo, que la ficha trae en `ogname` o en el <title>."""
    m = re.search(r'var ogname = "([^"]+)"', pagina) or \
        re.search(r"<title>\s*Download\s+(.*?)</title>", pagina, re.S | re.I)
    return _limpio(m.group(1)) if m else ""


async def enlace_directo(s: aiohttp.ClientSession, ficha: str) -> Optional[Tuple[str, str]]:
    """Resuelve /f/<id> a (URL firmada del CDN, nombre del archivo). Misma cadena que el botón Download."""
    pagina = await _get(s, ficha)
    if not pagina:
        return None
    file_id = re.search(r'data-file-id="(\d+)"', pagina)
    boton = re.search(r'href="(https://dl\.[^"]+/file/\d+)"', pagina)
    if not (file_id and boton):
        return None
    base = "{0.scheme}://{0.netloc}".format(urlparse(boton.group(1)))
    async with s.post(f"{base}/api/_001_v2", json={"id": file_id.group(1)}) as r:
        if r.status != 200:
            return None
        meta = await r.json(content_type=None)
    ruta = str(meta.get("mediafiles") or "") + str(meta.get("path") or "")
    if not ruta.startswith("http"):
        return None
    async with s.get(FIRMA, params={"path": unquote(urlparse(ruta).path)}) as r:
        if r.status != 200:
            return None
        firma = await r.json(content_type=None)
    if not firma.get("token"):
        return None
    nombre = str(meta.get("original") or "") or nombre_de_ficha(pagina)
    return f"{ruta}?token={firma['token']}&ex={firma.get('ex', '')}", nombre


async def _resolver(s: aiohttp.ClientSession, ficha: str, sem: asyncio.Semaphore,
                    log_callback: Callable[[str], None]) -> Optional[Tuple[str, str]]:
    async with sem:
        try:
            return await enlace_directo(s, ficha)
        except Exception as e:
            log_callback(f"[WARN] Bunkr: no pude resolver {ficha}: {e}")
            return None


def url_de_album(url: str) -> str:
    """Convierte un enlace de Bunkr Search (`/albums/<id>`) en el álbum de bunkr.cr."""
    if "bunkrsearch.com" in urlparse(url).netloc:
        m = re.search(r"/albums/([A-Za-z0-9]+)", urlparse(url).path)
        return f"{WEB}/a/{m.group(1)}" if m else ""
    return url


async def listar_videos(url: str) -> Tuple[str, List[Tuple[str, str, str]]]:
    """(título del álbum, [(nombre, ficha, tamaño)] de sus vídeos). Para el menú del bot."""
    url = url_de_album(url)
    if not url:
        return "", []
    timeout = aiohttp.ClientTimeout(total=45, connect=10)
    async with aiohttp.ClientSession(headers=CABECERAS, timeout=timeout) as s:
        pagina = await _get(s, url)
    if not pagina:
        return "", []
    return titulo_de(pagina, "Bunkr"), videos_del_album(pagina)


async def descargar_videos(titulo: str, items: List[Tuple[str, str, str]],
                           log_callback: Callable[[str], None],
                           check_cancel: Optional[Callable[[], bool]] = None) -> None:
    """Descarga los vídeos elegidos en el menú del álbum (ítems de `listar_videos`)."""
    timeout = aiohttp.ClientTimeout(total=45, connect=10)
    async with aiohttp.ClientSession(headers=CABECERAS, timeout=timeout) as s:
        for i, (nombre, ficha, _tam) in enumerate(items, 1):
            if check_cancel and check_cancel():
                break
            try:
                resuelto = await enlace_directo(s, ficha)
            except Exception as e:
                log_callback(f"[WARN] Bunkr: no pude resolver {ficha}: {e}")
                continue
            if not resuelto:
                continue
            directo, nombre_real = resuelto
            nombre = nombre or nombre_real
            ext = extension_de(nombre) or "mp4"
            indice = i if len(items) > 1 else None
            destino = os.path.join(carpeta_media(), nombre_media(titulo, ext, indice))
            log_callback(f"[INFO] Vídeo {i}/{len(items)}: {os.path.basename(destino)}")
            await descargar_archivo(directo, destino, CABECERAS, log_callback, check_cancel)


class BunkrHandler(BaseSiteHandler):
    """Handler para Bunkr (solo vídeos → MP4)."""

    @staticmethod
    def get_supported_domains() -> list:
        return DOMINIOS

    async def process(self, url: str, log_callback: Callable[[str], None], check_cancel: Callable[[], bool],
                      progress_callback: Optional[Callable[[int, int], None]] = None):
        log_callback("[INIT] Procesando Bunkr...")
        url = url_de_album(url)
        if not url:
            log_callback("[ERROR] Enlace de Bunkr Search no reconocido.")
            return None
        timeout = aiohttp.ClientTimeout(total=45, connect=10)
        async with aiohttp.ClientSession(headers=CABECERAS, timeout=timeout) as s:
            if _RE_FICHA.search(url) and not _RE_ALBUM.search(url):
                # Enlace directo a un archivo
                resuelto = await enlace_directo(s, url)
                if not resuelto:
                    log_callback("[ERROR] Bunkr no devolvió el enlace de descarga del archivo.")
                    return None
                directo, nombre = resuelto
                if not nombre:
                    # Sin nombre en la ficha: el id de la URL no trae extensión, así que no se puede
                    # saber el tipo. Se avisa en vez de guardar un archivo sin extensión.
                    log_callback("[ERROR] Bunkr no indicó el nombre del archivo.")
                    return None
                titulo = f"Bunkr {os.path.splitext(nombre)[0]}"
                return await self._bajar_uno(directo, titulo, nombre, log_callback, check_cancel)

            pagina = await _get(s, url)
            if not pagina:
                log_callback("[ERROR] Bunkr no respondió (álbum borrado o sitio caído).")
                return None

            if not _RE_ALBUM.search(url):
                # Perfil/listado: devolver los archivos para encolarlos
                enlaces = [urljoin(WEB + "/", u) for u in re.findall(r'href="(/f/[A-Za-z0-9]+)"', pagina)]
                enlaces = list(dict.fromkeys(enlaces))[:MAX_ARCHIVOS]
                if not enlaces:
                    log_callback("[ERROR] No encontré archivos en esa página de Bunkr.")
                    return None
                log_callback(f"[INFO] Bunkr: {len(enlaces)} archivo(s).")
                return enlaces

            titulo = titulo_de(pagina, "Bunkr")
            videos = videos_del_album(pagina)
            log_callback(f"[INFO] Title detected: {titulo}")
            if not videos:
                log_callback("[ERROR] El álbum no tiene vídeos (las fotos no se descargan).")
                return None

            log_callback(f"[INFO] {len(videos)} vídeo(s): se guardan como MP4.")
            sem = asyncio.Semaphore(5)
            for i, (nombre, ficha, _tam) in enumerate(videos, 1):
                if check_cancel and check_cancel():
                    break
                resuelto = await _resolver(s, ficha, sem, log_callback)
                if not resuelto:
                    continue
                directo, nombre_real = resuelto
                # El nombre del listado del álbum manda; el de la ficha es el respaldo
                nombre = nombre or nombre_real
                ext = extension_de(nombre) or "mp4"
                indice = i if len(videos) > 1 else None
                destino = os.path.join(carpeta_media(), nombre_media(titulo, ext, indice))
                log_callback(f"[INFO] Vídeo {i}/{len(videos)}: {os.path.basename(destino)}")
                await descargar_archivo(directo, destino, CABECERAS, log_callback, check_cancel)

        return None

    async def _bajar_uno(self, directo: str, titulo: str, nombre: str,
                         log_callback, check_cancel):
        ext = extension_de(nombre)
        if ext in _EXT_VIDEO:
            destino = os.path.join(carpeta_media(), nombre_media(titulo, ext))
            log_callback(f"[INFO] Vídeo 1/1: {os.path.basename(destino)}")
            await descargar_archivo(directo, destino, CABECERAS, log_callback, check_cancel)
        else:
            log_callback(f"[ERROR] Solo se descargan vídeos; .{ext or '?'} no lo es.")
        return None
