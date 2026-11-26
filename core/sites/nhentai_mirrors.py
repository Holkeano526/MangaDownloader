"""
Clones/mirrors de nhentai (nhentai.to, nhentai.xxx, nhentai.website, ...) — genérico y por HTTP.

Comparten el mismo frontend con variaciones. En vez de un handler por dominio, se
aprende el patrón de cada uno en el momento:

  1. Ficha de la galería (/g/<id>/ o /read/<id>): título en <h1> y miniaturas numeradas
     "<base>/<n><sufijo>.<ext>" (sufijo "t" en nhentai.to/.xxx, "x" en nhentai.website...).
  2. Lector de la página 1 (enlace ".../<id>/1" de la ficha): imagen completa "<base2>/1.<ext2>".
     De ahí sale la plantilla de URL (el servidor y la extensión pueden ser otros que los
     de la miniatura).
  3. El TOTAL no se saca de las miniaturas (algunos mirrors solo cargan las primeras: p. ej.
     nhentai.website muestra 20 de 50): se busca la última página que existe en el servidor de
     imágenes (potencias de 2 en paralelo + búsqueda binaria, con peticiones HEAD).
  4. Cada página se comprueba con HEAD probando su extensión y luego jpg/png/webp/gif (nhentai
     original mezcla formatos por página). Si ninguna existe, se lee esa página del lector.

Visto el 25/11/2026:
  nhentai.to       /g/<id>      supercdn.site/galleries/<media>/<n>t.jpg -> <n>.jpg
  nhentai.xxx      /g/<id>/     i5.nhentaimg.com/<dir>/<hash>/<n>t.jpg   -> <n>.webp
  nhentai.website  /read/<id>   t1.nhentai.jp.net/g/<media>/<n>x.webp    -> (lector)

Los mirrors con desafío de Cloudflare (p. ej. nhentai.top) no se intentan saltar: se
avisa con un mensaje claro. nhentai.net sigue en su handler propio (nhentai.py).
"""
import asyncio
import html
import re
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import aiohttp

from .base import BaseSiteHandler
from .. import config
from ..utils import clean_filename, download_and_make_pdf

EXT = r"(?:jpg|jpeg|png|webp|gif)"


class ErrorMirror(Exception):
    pass


def _es_cloudflare(status: int, cuerpo: str) -> bool:
    return status in (403, 503) and bool(re.search(r"just a moment|cf-chl|challenge-platform|cf_chl", cuerpo, re.I))


class NHentaiMirrorHandler(BaseSiteHandler):
    """Cualquier dominio "nhentai.*" salvo nhentai.net (ese tiene su propio handler)."""

    @staticmethod
    def get_supported_domains() -> list:
        return ["nhentai"]

    async def process(
        self,
        url: str,
        log_callback: Callable[[str], None],
        check_cancel: Callable[[], bool],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        p = urlparse(url)
        origen = f"{p.scheme or 'https'}://{p.netloc}"
        m = re.search(r"/(g|read)/(\d+)", p.path)
        if not m:
            log_callback(f"[ERROR] Esa URL no es una galería. Pásame el enlace de un manga de {p.netloc} "
                         f"(del tipo {origen}/g/123456/ o {origen}/read/123456).")
            return
        ruta, gid = m.group(1), m.group(2)
        ficha_url = f"{origen}/{ruta}/{gid}" + ("/" if url.rstrip().endswith("/") or ruta == "g" else "")
        log_callback(f"[INIT] Processing {p.netloc} gallery {gid}...")

        headers = {"User-Agent": config.USER_AGENT, "Referer": f"{origen}/"}
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        try:
            async with aiohttp.ClientSession(headers=headers, timeout=timeout) as s:
                titulo, urls = await self._resolver(s, origen, ficha_url, gid, log_callback)
        except ErrorMirror as e:
            log_callback(f"[ERROR] {e}")
            return
        except aiohttp.ClientError as e:
            log_callback(f"[ERROR] No se pudo conectar con {p.netloc}: {e}")
            return

        log_callback(f"[INFO] Title detected: {titulo}")
        log_callback(f"[INFO] Images detected: {len(urls)}")
        await download_and_make_pdf(
            urls,
            f"{clean_filename(titulo)}.pdf",
            headers,
            log_callback,
            check_cancel,
            progress_callback=progress_callback,
            open_result=config.OPEN_RESULT_ON_FINISH,
        )

    # ------------------------------------------------------------------------
    async def _get(self, s: aiohttp.ClientSession, url: str) -> Tuple[str, str]:
        async with s.get(url, allow_redirects=True) as r:
            cuerpo = await r.text(errors="replace")
            if _es_cloudflare(r.status, cuerpo):
                raise ErrorMirror(f"{urlparse(url).netloc} está protegido por Cloudflare (desafío anti-bots) "
                                  f"y no se puede descargar automáticamente. Prueba con otro mirror de nhentai.")
            if r.status == 404:
                raise ErrorMirror(f"La galería no existe en {urlparse(url).netloc} (404). Los IDs cambian entre mirrors.")
            if r.status != 200:
                raise ErrorMirror(f"{urlparse(url).netloc} respondió {r.status}.")
            return str(r.url), cuerpo

    @staticmethod
    def _titulo(pagina: str, gid: str) -> str:
        m = re.search(r"<h1[^>]*>(.*?)</h1>", pagina, re.S | re.I)
        t = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip() if m else ""
        return re.sub(r"\s+", " ", t) or f"nhentai_{gid}"

    @staticmethod
    def _miniaturas(pagina: str, base_url: str) -> Dict[int, Tuple[str, str]]:
        """{n: (prefijo, ext)} de las miniaturas numeradas de la ficha."""
        res: Dict[int, Tuple[str, str]] = {}
        for url in re.findall(rf'(?:data-src|src)="([^"]+?/\d+[a-z]?\.{EXT})"', pagina, re.I):
            m = re.match(rf"(.+/)(\d+)([a-z]?)\.({EXT})$", urljoin(base_url, url), re.I)
            if m and m.group(3):  # solo miniaturas (llevan sufijo: 1t, 1x...)
                res.setdefault(int(m.group(2)), (m.group(1), m.group(4).lower()))
        return res

    async def _resolver(self, s, origen: str, ficha_url: str, gid: str,
                        log_callback: Callable[[str], None]) -> Tuple[str, List[str]]:
        ficha_final, ficha = await self._get(s, ficha_url)
        titulo = self._titulo(ficha, gid)
        minis = self._miniaturas(ficha, ficha_final)
        if not minis:
            raise ErrorMirror("No encontré las miniaturas de la galería: el mirror usa un formato desconocido.")

        # Enlace del lector a la página 1 (vale /g/<id>/1/ y /read/<id>/1)
        m = re.search(rf'href="([^"]*/{gid}/1/?)"', ficha)
        lector_tpl = urljoin(ficha_final, m.group(1)) if m else None
        if not lector_tpl:
            raise ErrorMirror("No encontré el lector de la galería en el mirror.")

        async def imagen_de_pagina(n: int) -> Optional[str]:
            u = re.sub(r"/1/?$", lambda x: f"/{n}" + ("/" if x.group(0).endswith("/") else ""), lector_tpl)
            final, pag = await self._get(s, u)
            # La imagen completa: termina en /<n>.<ext> sin sufijo de miniatura
            for cand in re.findall(rf'(?:data-src|src)="([^"]+?/{n}\.{EXT})"', pag, re.I):
                return urljoin(final, cand)
            return None

        img1 = await imagen_de_pagina(1)
        if not img1:
            raise ErrorMirror("El lector no muestra la imagen de la página 1.")
        m1 = re.match(rf"(.+/)1\.({EXT})$", img1, re.I)
        prefijo, ext1 = m1.group(1), m1.group(2).lower()
        mismo_formato = minis.get(1, ("", ""))[1] == ext1  # el mirror conserva el formato de la miniatura
        sem = asyncio.Semaphore(8)

        async def head_ok(u: str) -> bool:
            async with sem:
                try:
                    async with s.head(u, allow_redirects=True) as r:
                        return r.status == 200 and r.content_type.startswith("image/")
                except aiohttp.ClientError:
                    return False

        def preferida(n: int) -> str:
            """Formato más probable de la página n: el de su miniatura si el mirror lo conserva."""
            return minis[n][1] if mismo_formato and n in minis else ext1

        async def ext_de(n: int, todas: bool = False) -> Optional[str]:
            """Extensión con la que existe la página n (None si no existe).
            Para buscar el total basta con las 2 más probables; todas=True prueba el resto."""
            opciones = [preferida(n)] + [e for e in ("jpg", "png", "webp", "gif", "jpeg") if e != preferida(n)]
            for e in (opciones if todas else opciones[:2]):
                if await head_ok(f"{prefijo}{n}.{e}"):
                    return e
            return None

        # Total real. Atajo: si la ficha muestra todas las miniaturas, la siguiente no existe
        # y basta 1 comprobación (nhentai.to). Si existe, potencias de 2 y búsqueda binaria.
        bajo = max(minis)
        if await ext_de(bajo) and not await ext_de(bajo + 1):
            log_callback(f"[INFO] {bajo} páginas (todas las miniaturas visibles).")
            return titulo, await self._urls(bajo, prefijo, preferida, minis, mismo_formato, ext_de, imagen_de_pagina)
        if not await ext_de(bajo):
            bajo = 1
        saltos = [bajo * (2 ** k) for k in range(1, 8)]  # hasta x128
        existen = await asyncio.gather(*(ext_de(n) for n in saltos))
        alto = next((n for n, e in zip(saltos, existen) if not e), saltos[-1] + 1)
        bajo = max([bajo] + [n for n, e in zip(saltos, existen) if e and n < alto])
        while alto - bajo > 1:
            medio = (bajo + alto) // 2
            if await ext_de(medio):
                bajo = medio
            else:
                alto = medio
        total = bajo
        log_callback(f"[INFO] {total} páginas en el servidor (miniaturas visibles: {max(minis)}).")

        return titulo, await self._urls(total, prefijo, preferida, minis, mismo_formato, ext_de, imagen_de_pagina)

    @staticmethod
    async def _urls(total, prefijo, preferida, minis, mismo_formato, ext_de, imagen_de_pagina) -> List[str]:
        """URL de cada página con su formato más probable (sin comprobar una a una: lento).
        Solo se verifican las páginas SIN miniatura propia en mirrors que mezclan formatos;
        si una no existe así, se prueban los demás formatos y por último su lector."""
        urls = [f"{prefijo}{n}.{preferida(n)}" for n in range(1, total + 1)]
        dudosas = [n for n in range(1, total + 1) if mismo_formato and n not in minis]
        if dudosas:
            exts = await asyncio.gather(*(ext_de(n, todas=True) for n in dudosas))
            for n, e in zip(dudosas, exts):
                if e:
                    urls[n - 1] = f"{prefijo}{n}.{e}"
                else:
                    urls[n - 1] = await imagen_de_pagina(n) or urls[n - 1]
        return urls
