"""
LectorHentai — descarga por HTTP, con navegador solo como respaldo.

  Ficha : https://lectorhentai.com/manga/<id>/<slug>
  Lector: https://lectorhentai.com/read/<id>/<slug>
  Páginas: https://img1.giolandscaping.com/library/<id>/01.jpg, 02.jpg, ...

El lector descarga las imágenes con fetch() y las muestra como blob: (reader-blob.js,
ofuscado), así que no aparecen en el HTML ni hay un número de páginas legible.
Las imágenes sí se sirven directamente con Referer: https://lectorhentai.com/
(sin él: 403). Estrategia:
  1. Detectar el formato del nombre de la página 1 (01.jpg, 1.jpg, 001.jpg, .png, .webp...).
  2. Encontrar la última página con búsqueda exponencial + binaria (~10 peticiones HEAD).
  3. Descargar en paralelo con el motor común (reintentos incluidos).
Si el patrón no encaja, se abre el lector en Chromium y se recogen las URLs que pide.
"""
import asyncio
import html
import re
from typing import Callable, List, Optional, Tuple

import aiohttp

from .base import BaseSiteHandler
from .. import config
from ..utils import clean_filename, download_and_make_pdf

WEB = "https://lectorhentai.com"
IMG = "https://img1.giolandscaping.com/library"
FORMATOS_NUMERO = ["{:02d}", "{:d}", "{:03d}"]
EXTENSIONES = ["jpg", "png", "webp", "jpeg"]
MAX_PAGINAS = 2000


class LectorHentaiHandler(BaseSiteHandler):
    """Handler para lectorhentai.com."""

    @staticmethod
    def get_supported_domains() -> list:
        return ["lectorhentai.com"]

    async def process(
        self,
        url: str,
        log_callback: Callable[[str], None],
        check_cancel: Callable[[], bool],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        m = re.search(r"/(?:manga|read)/(\d+)(?:/([^/?#]+))?", url)
        if not m:
            log_callback("[ERROR] No encuentro el número del manga en la URL de LectorHentai.")
            return
        manga_id, slug = m.group(1), m.group(2) or ""
        log_callback(f"[INIT] Processing LectorHentai ID: {manga_id}...")

        headers = {"User-Agent": config.USER_AGENT, "Referer": f"{WEB}/"}
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as s:
            # Título y páginas a la vez (son peticiones independientes)
            titulo, urls = await asyncio.gather(self._titulo(s, manga_id, slug), self._resolver_http(s, manga_id))
            log_callback(f"[INFO] Title detected: {titulo}")

        if not urls:
            log_callback("[WARN] Las páginas no siguen el patrón conocido. Abriendo el lector en el navegador...")
            urls = await self._resolver_navegador(manga_id, slug, log_callback)
        if not urls:
            log_callback("[ERROR] No encontré imágenes en LectorHentai.")
            return

        log_callback(f"[INFO] Images detected: {len(urls)}")
        await download_and_make_pdf(
            urls,
            f"{clean_filename(titulo)}.pdf",
            {"User-Agent": config.USER_AGENT, "Referer": f"{WEB}/"},
            log_callback,
            check_cancel,
            progress_callback=progress_callback,
            open_result=config.OPEN_RESULT_ON_FINISH,
        )

    # --- Título -------------------------------------------------------------
    @staticmethod
    async def _titulo(s: aiohttp.ClientSession, manga_id: str, slug: str) -> str:
        try:
            async with s.get(f"{WEB}/manga/{manga_id}/{slug}") as r:
                pagina = await r.text() if r.status == 200 else ""
            m = re.search(r'<h1[^>]*class="entry-title"[^>]*>(.*?)</h1>', pagina, re.S)
            if m:
                t = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
                t = re.sub(r"\s+en Español.*$", "", t, flags=re.I)
                t = re.sub(r"^Leer\s+", "", t, flags=re.I)
                if t:
                    return t
        except (aiohttp.ClientError, asyncio.TimeoutError):
            pass
        return slug.replace("-", " ").title() or f"LectorHentai_{manga_id}"

    # --- Resolución por HTTP --------------------------------------------------
    @staticmethod
    async def _existe(s: aiohttp.ClientSession, url: str) -> bool:
        try:
            async with s.head(url, allow_redirects=True) as r:
                if r.status == 405:  # HEAD no permitido: probar con GET de 1 byte
                    async with s.get(url, headers={"Range": "bytes=0-0"}) as g:
                        return g.status in (200, 206) and g.content_type.startswith("image/")
                return r.status == 200 and r.content_type.startswith("image/")
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return False

    async def _resolver_http(self, s: aiohttp.ClientSession, manga_id: str) -> List[str]:
        formato = await self._detectar_formato(s, manga_id)
        if not formato:
            return []
        fmt_num, ext = formato
        url_de = lambda n: f"{IMG}/{manga_id}/{fmt_num.format(n)}.{ext}"

        # Potencias de 2 en paralelo (2, 4, 8 ... 2048): la primera que no existe acota el final
        potencias = [2 ** k for k in range(1, 12)]
        existen = await asyncio.gather(*(self._existe(s, url_de(n)) for n in potencias))
        alto = next((n for n, ok in zip(potencias, existen) if not ok), MAX_PAGINAS + 1)
        bajo = alto // 2 if alto > 2 else 1  # bajo existe, alto no
        # Búsqueda binaria entre ambos (~log2 peticiones)
        while alto - bajo > 1:
            medio = (bajo + alto) // 2
            if await self._existe(s, url_de(medio)):
                bajo = medio
            else:
                alto = medio
        return [url_de(n) for n in range(1, bajo + 1)]

    async def _detectar_formato(self, s: aiohttp.ClientSession, manga_id: str) -> Optional[Tuple[str, str]]:
        for fmt_num in FORMATOS_NUMERO:
            for ext in EXTENSIONES:
                if await self._existe(s, f"{IMG}/{manga_id}/{fmt_num.format(1)}.{ext}"):
                    return fmt_num, ext
        return None

    # --- Respaldo con navegador ------------------------------------------------
    @staticmethod
    async def _resolver_navegador(manga_id: str, slug: str, log_callback: Callable[[str], None]) -> List[str]:
        from playwright.async_api import async_playwright
        vistas: List[str] = []
        try:
            async with async_playwright() as p:
                b = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
                pg = await (await b.new_context(user_agent=config.USER_AGENT)).new_page()
                pg.on("request", lambda r: vistas.append(r.url)
                      if f"/library/{manga_id}/" in r.url and "cover" not in r.url else None)
                await pg.goto(f"{WEB}/read/{manga_id}/{slug}", wait_until="networkidle", timeout=60000)
                # El lector va página a página (precarga 2). El total sale de su selector "1/N";
                # se elige cada página en ese selector para que pida su imagen.
                total = await pg.evaluate(r"""() => {
                    for (const o of document.querySelectorAll('option')) {
                        const m = (o.textContent || '').trim().match(/^\d+\s*\/\s*(\d+)$/);
                        if (m) return parseInt(m[1]);
                    }
                    return 0;
                }""")
                log_callback(f"[INFO] Lector en navegador: {total or '?'} páginas")
                for n in range(2, (total or 0) + 1):
                    await pg.evaluate(r"""(n) => {
                        for (const s of document.querySelectorAll('select')) {
                            const o = [...s.options].find(o => (o.textContent || '').trim().startsWith(n + '/'));
                            if (o) { s.value = o.value; s.dispatchEvent(new Event('change', {bubbles: true})); }
                        }
                    }""", n)
                    await pg.wait_for_timeout(350)
                await pg.wait_for_timeout(1500)
                await b.close()
        except Exception as e:
            log_callback(f"[ERROR] Navegador: {e}")
        urls = list(dict.fromkeys(vistas))

        def _num(u: str) -> int:
            m = re.search(r"/(\d+)\.\w+$", u)
            return int(m.group(1)) if m else 0

        return sorted(urls, key=_num)
