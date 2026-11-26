"""
HentaiNexus — descarga por HTTP, sin navegador.

  Ficha : https://hentainexus.com/view/<id>
  Lector: https://hentainexus.com/read/<id>

El lector recibe la lista de páginas CIFRADA: initReader("<base64>", "<título>", <id>, {...}).
reader.min.js la descifra así (portado tal cual, revisado contra ?r=31):
  1. base64 -> bytes; XOR de los primeros min(len(hostname), 64) bytes con el hostname.
  2. Los 64 primeros bytes son la clave. C = XOR de la clave pasado por un CRC de
     polinomio 0xC (8 rondas por byte); se usa C & 7 para elegir uno de los 16 primeros primos.
  3. Cifrado de flujo tipo RC4 (KSA con la clave; PRGA modificado con ese primo como paso)
     sobre los bytes 64.. -> JSON con una entrada por página.
     Formato actual (r=31): {"type": "image", "image_fallback": <webp>, "image_avif": <avif>,
     "label": "1", ...}. Versiones anteriores usaban "image". Resolución completa (p. ej. 2259x3200).
Si el sitio cambia el cifrado, se lanza ErrorDescifrado y el handler usa el navegador.
"""
import base64
import json
import re
from typing import Callable, List, Optional, Tuple

import aiohttp

from .base import BaseSiteHandler
from .. import config
from ..http import crear_sesion
from ..utils import clean_filename, download_and_make_pdf

WEB = "https://hentainexus.com"
HOSTNAME = "hentainexus.com"


class ErrorDescifrado(Exception):
    pass


def _primos(n: int) -> List[int]:
    """Los n primeros primos con la misma criba que reader.min.js."""
    marcados, primos, i = set(), [], 2
    while len(primos) < n:
        if i not in marcados:
            primos.append(i)
            marcados.update(range(i * 2, 257, i))
        i += 1
    return primos


def descifrar(datos_b64: str, hostname: str = HOSTNAME) -> list:
    blob = bytearray(base64.b64decode(datos_b64))
    for i in range(min(len(hostname), 64)):
        blob[i] ^= ord(hostname[i])
    if len(blob) <= 64:
        raise ErrorDescifrado("bloque cifrado demasiado corto")

    c = 0
    for i in range(64):
        c ^= blob[i]
        for _ in range(8):
            c = (c >> 1) ^ 0xC if c & 1 else c >> 1
    paso = _primos(16)[c & 7]

    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + blob[i % 64]) % 256
        s[i], s[j] = s[j], s[i]

    salida = bytearray()
    a = x = cc = m = 0
    for n in range(len(blob) - 64):
        a = (a + paso) % 256
        x = (cc + s[(x + s[a]) % 256]) % 256
        cc = (cc + a + s[a]) % 256
        s[a], s[x] = s[x], s[a]
        m = s[(x + s[(a + s[(m + cc) % 256]) % 256]) % 256]
        salida.append(blob[n + 64] ^ m)
    try:
        # El JS construye la cadena con String.fromCharCode byte a byte: latin-1 -> luego JSON
        return json.loads(salida.decode("latin-1"))
    except ValueError as e:
        raise ErrorDescifrado(f"el resultado no es JSON ({e})")


def _url_pagina(p: dict) -> str:
    """WebP primero (img2pdf/Pillow lo convierten); AVIF solo si no hay otra cosa."""
    return p.get("image") or p.get("image_fallback") or p.get("image_avif") or ""


class HentaiNexusHandler(BaseSiteHandler):
    """Handler para hentainexus.com."""

    @staticmethod
    def get_supported_domains() -> list:
        return ["hentainexus.com"]

    async def process(
        self,
        url: str,
        log_callback: Callable[[str], None],
        check_cancel: Callable[[], bool],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        m = re.search(r"/(?:view|read)/(\d+)", url)
        if not m:
            log_callback("[ERROR] No encuentro el número de la galería en la URL de HentaiNexus.")
            return
        gid = m.group(1)
        log_callback(f"[INIT] Processing HentaiNexus ID: {gid}...")

        try:
            titulo, urls = await self._resolver_http(gid)
            log_callback(f"[INFO] Resolved without browser: {len(urls)} images.")
        except Exception as e:
            log_callback(f"[WARN] Descifrado HTTP falló ({e}). Abriendo el lector en el navegador...")
            titulo, urls = await self._resolver_navegador(gid, log_callback)
        if not urls:
            log_callback("[ERROR] No encontré imágenes en HentaiNexus.")
            return

        log_callback(f"[INFO] Title detected: {titulo}")
        log_callback(f"[INFO] Images detected: {len(urls)}")
        await download_and_make_pdf(
            urls,
            f"{clean_filename(titulo)}.pdf",
            {"User-Agent": config.USER_AGENT, "Referer": f"{WEB}/read/{gid}"},
            log_callback,
            check_cancel,
            progress_callback=progress_callback,
            open_result=config.OPEN_RESULT_ON_FINISH,
        )

    @staticmethod
    def _limpiar_titulo(t: str, gid: str) -> str:
        t = re.sub(r"\s*::\s*HentaiNexus\s*$", "", t or "").strip()
        t = re.sub(r"^\[\d+/\d+\]\s*", "", t)  # la pestaña del lector antepone "[1/41] "
        return t or f"HentaiNexus_{gid}"

    async def _resolver_http(self, gid: str) -> Tuple[str, List[str]]:
        headers = {"User-Agent": config.USER_AGENT, "Referer": f"{WEB}/view/{gid}"}
        async with crear_sesion(headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as s:
            async with s.get(f"{WEB}/read/{gid}") as r:
                if r.status != 200:
                    raise ErrorDescifrado(f"el lector respondió {r.status}")
                pagina = await r.text()
        m = re.search(r'initReader\("([A-Za-z0-9+/=]+)",\s*"((?:[^"\\]|\\.)*)"', pagina)
        if not m:
            raise ErrorDescifrado("no hay initReader en el lector")
        datos = descifrar(m.group(1))
        urls = [_url_pagina(p) for p in datos if isinstance(p, dict) and p.get("type", "image") == "image"]
        urls = [u for u in urls if u]
        if not urls:
            raise ErrorDescifrado("la lista descifrada no trae imágenes")
        titulo = self._limpiar_titulo(json.loads(f'"{m.group(2)}"'), gid)
        return titulo, urls

    async def _resolver_navegador(self, gid: str, log_callback: Callable[[str], None]) -> Tuple[str, List[str]]:
        """Respaldo: deja que el JS del sitio descifre y lee su variable global pageData."""
        from playwright.async_api import async_playwright
        try:
            async with async_playwright() as p:
                b = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
                pg = await (await b.new_context(user_agent=config.USER_AGENT)).new_page()
                await pg.goto(f"{WEB}/read/{gid}", wait_until="domcontentloaded", timeout=60000)
                await pg.wait_for_function("() => Array.isArray(window.pageData) && window.pageData.length > 0", timeout=20000)
                urls = await pg.evaluate("() => window.pageData.map(p => p.image || p.image_fallback || p.image_avif).filter(Boolean)")
                titulo = self._limpiar_titulo(await pg.title(), gid)
                await b.close()
                return titulo, urls
        except Exception as e:
            log_callback(f"[ERROR] Navegador: {e}")
            return f"HentaiNexus_{gid}", []
