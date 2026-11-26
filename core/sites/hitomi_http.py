"""
Resolución de galerías de Hitomi.la SIN navegador.

Hitomi publica dos ficheros que bastan para construir las URLs de las imágenes:
  - galleries/<id>.js : la ficha de la galería (título y lista de imágenes con su hash).
  - gg.js             : las reglas que reparten las imágenes entre servidores (w1, w2...)
                        y el prefijo de ruta. Cambia periódicamente: se lee en cada descarga.

Es la misma lógica que url_from_url_from_hash() de su common.js (dir/ext = "webp"):

  ruta     = https://a.<dominio>/<gg.b><gg.s(hash)>/<hash>.webp
  gg.s(h)  = int(h[-1] + h[-3:-1], 16) en decimal
  servidor = "w" + (1 + gg.m(g)),  g = int(h[-1] + h[-3:-1], 16)
  gg.m(g)  = el valor del "case" de gg.js que contenga g, o el valor por defecto

Antes se abría un Chromium completo (Playwright) solo para ejecutar ese JavaScript:
2,5-3 s y ~300 MB de RAM por descarga. Esto tarda ~0,4 s. Si Hitomi cambia el
formato y algo no cuadra, se lanza ErrorResolucion y el handler vuelve a Playwright.
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Tuple

import aiohttp

DOMINIO = "gold-usergeneratedcontent.net"
LTN = f"https://ltn.{DOMINIO}"


class ErrorResolucion(Exception):
    """El formato de Hitomi no es el esperado: hay que usar el navegador."""


class ReglasGG:
    """gg.js interpretado: prefijo b, valor por defecto y tabla de casos de m()."""

    def __init__(self, b: str, por_defecto: int, casos: Dict[int, int]):
        self.b = b
        self.por_defecto = por_defecto
        self.casos = casos

    def m(self, g: int) -> int:
        return self.casos.get(g, self.por_defecto)

    @staticmethod
    def s(h: str) -> str:
        return str(int(h[-1] + h[-3:-1], 16))

    @classmethod
    def desde_js(cls, js: str) -> "ReglasGG":
        mb = re.search(r"b:\s*'(\d+/)'", js)
        md = re.search(r"var o = (\d+)", js)
        if not mb or not md:
            raise ErrorResolucion("gg.js sin 'b' o sin valor por defecto")
        casos: Dict[int, int] = {}
        pendientes: List[int] = []
        # Recorre el switch en orden: los "case N:" se acumulan hasta el "o = X; break;"
        for tok in re.finditer(r"case (\d+):|o = (\d+); break;", js):
            if tok.group(1) is not None:
                pendientes.append(int(tok.group(1)))
            else:
                valor = int(tok.group(2))
                for c in pendientes:
                    casos[c] = valor
                pendientes = []
        if not casos:
            raise ErrorResolucion("gg.js sin casos")
        return cls(mb.group(1), int(md.group(1)), casos)


def url_imagen(h: str, reglas: ReglasGG, ext: str = "webp") -> str:
    """URL final de una imagen (equivale a url_from_url_from_hash(id, img, ext, ext))."""
    if not re.fullmatch(r"[0-9a-f]{64}", h):
        raise ErrorResolucion(f"hash con formato inesperado: {h[:16]}...")
    g = int(h[-1] + h[-3:-1], 16)
    sub = f"{ext[0]}{1 + reglas.m(g)}"   # webp -> w1/w2, avif -> a1/a2
    return f"https://{sub}.{DOMINIO}/{reglas.b}{reglas.s(h)}/{h}.{ext}"


async def resolver(gallery_id: int, session: aiohttp.ClientSession) -> Tuple[str, List[str]]:
    """Devuelve (título como lo mostraba el lector, lista de URLs webp en orden)."""
    cab = {"Referer": "https://hitomi.la/"}
    async with session.get(f"{LTN}/galleries/{gallery_id}.js", headers=cab) as r:
        if r.status != 200:
            raise ErrorResolucion(f"galleries/{gallery_id}.js devolvió {r.status}")
        texto = await r.text()
    async with session.get(f"{LTN}/gg.js", headers=cab) as r:
        if r.status != 200:
            raise ErrorResolucion(f"gg.js devolvió {r.status}")
        gg = await r.text()

    i = texto.find("{")
    if i < 0:
        raise ErrorResolucion("galleries.js sin JSON")
    info = json.loads(texto[i:])
    archivos = info.get("files") or []
    if not archivos:
        raise ErrorResolucion("galería sin imágenes")
    reglas = ReglasGG.desde_js(gg)
    urls = [url_imagen(f["hash"], reglas) for f in archivos]
    # El lector titula la pestaña "<título> | Hitomi.la": se conserva para que el PDF
    # se llame igual que con el método del navegador.
    titulo = f"{info.get('title') or 'Hitomi_' + str(gallery_id)} | Hitomi.la"
    return titulo, urls
