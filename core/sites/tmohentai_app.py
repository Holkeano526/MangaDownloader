"""
TMOHentai (dominio .app) — descarga por HTTP, sin navegador.

  Ficha : https://tmohentai.app/library/<tipo>/<id>/<slug>
  Lector: https://tmohentai.app/view_uploads/<id>
  Páginas: https://storage.tmohentai.app/mangas/<id>/<n>.webp  (todas en el HTML del lector)

El handler antiguo de TMO (tmo.py) usa crawl4ai + Gemini para el dominio .com y no
entiende las URLs /library/ de .app; este va antes en la lista de handlers.
"""
import html
import re
from typing import Callable, Optional

import aiohttp

from .base import BaseSiteHandler
from .. import config
from ..utils import clean_filename, download_and_make_pdf

BASE = "https://tmohentai.app"


class TMOHentaiAppHandler(BaseSiteHandler):
    """Handler para tmohentai.app."""

    @staticmethod
    def get_supported_domains() -> list:
        return ["tmohentai.app"]

    async def process(
        self,
        url: str,
        log_callback: Callable[[str], None],
        check_cancel: Callable[[], bool],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        m = re.search(r"/(?:library/[^/]+|view_uploads|contents|reader)/(\d+)", url)
        if not m:
            log_callback("[ERROR] No encuentro el número del manga en la URL de TMOHentai.")
            return
        manga_id = m.group(1)
        log_callback(f"[INIT] Processing TMOHentai ID: {manga_id}...")

        headers = {"User-Agent": config.USER_AGENT, "Referer": f"{BASE}/"}
        timeout = aiohttp.ClientTimeout(total=60, connect=15)
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as s:
            async with s.get(f"{BASE}/view_uploads/{manga_id}") as r:
                if r.status != 200:
                    log_callback(f"[ERROR] El lector de TMOHentai respondió {r.status}.")
                    return
                lector = await r.text()
            titulo = self._titulo(lector)
            if not titulo and "/library/" in url:
                async with s.get(url) as r:
                    if r.status == 200:
                        titulo = self._titulo(await r.text())

        # URLs de páginas en orden de aparición, sin duplicados
        patron = rf"https://storage\.tmohentai\.app/mangas/{manga_id}/[^\"'\s<>]+\.(?:webp|jpe?g|png)"
        urls = list(dict.fromkeys(re.findall(patron, lector)))
        if not urls:
            log_callback("[ERROR] No encontré imágenes en el lector de TMOHentai.")
            return

        titulo = titulo or f"TMOHentai_{manga_id}"
        log_callback(f"[INFO] Title detected: {titulo}")
        log_callback(f"[INFO] Images detected: {len(urls)}")
        await download_and_make_pdf(
            urls,
            f"{clean_filename(titulo)}.pdf",
            {"User-Agent": config.USER_AGENT, "Referer": f"{BASE}/view_uploads/{manga_id}"},
            log_callback,
            check_cancel,
            progress_callback=progress_callback,
            open_result=config.OPEN_RESULT_ON_FINISH,
        )

    @staticmethod
    def _titulo(pagina: str) -> str:
        """'Ver <título> Online Gratis | TMOHentai' -> '<título>'."""
        m = re.search(r"<title>(.*?)</title>", pagina, re.S | re.I)
        if not m:
            return ""
        t = html.unescape(m.group(1)).strip()
        t = re.sub(r"\s*\|\s*TMOHentai\s*$", "", t, flags=re.I)
        t = re.sub(r"^(Ver|Leer)\s+", "", t, flags=re.I)
        t = re.sub(r"\s*[—–-]\s*Lector\s*$", "", t, flags=re.I)  # título del lector: "... — Lector"
        t = re.sub(r"\s+Online\s+Gratis\s*$", "", t, flags=re.I)
        return t.strip()
