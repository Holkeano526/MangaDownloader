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
from typing import Dict, List, Optional, Tuple

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


# ═══════════════════════════ Búsqueda en Hitomi ═══════════════════════════
# Hitomi no tiene un buscador por HTTP sencillo: su search.js consulta índices binarios.
#  - Palabras del título: árbol B en galleriesindex/galleries.<versión>.index (B=16).
#    Clave = primeros 4 bytes de sha256(palabra). Cada nodo (≤464 bytes, leído con Range):
#      nº claves (int32) + [tamaño (int32) + bytes] + nº datos (int32) + [offset (uint64) +
#      largo (int32)] + 17 direcciones de subnodos (uint64). Todo big-endian.
#    El dato apunta a galleries.<versión>.data: nº ids (int32) + ids (int32 cada uno).
#  - Artista/grupo/etiqueta: <tipo>/<nombre>-all.nozomi = lista de ids (int32 big-endian).
# Varias palabras = intersección. Ids más altos = más recientes.
import asyncio as _asyncio
import hashlib as _hashlib
import struct as _struct
import time as _time

TAM_NODO = 464


async def _rango(s: aiohttp.ClientSession, url: str, inicio: int, fin: int) -> bytes:
    cab = {"Range": f"bytes={inicio}-{fin}", "Referer": "https://hitomi.la/"}
    async with s.get(url, headers=cab) as r:
        if r.status not in (200, 206):
            raise ErrorResolucion(f"{url.rsplit('/', 1)[-1]} devolvió {r.status}")
        return await r.read()


def _nodo(datos: bytes):
    pos = 0
    def i32():
        nonlocal pos
        v = _struct.unpack_from(">i", datos, pos)[0]; pos += 4; return v
    def u64():
        nonlocal pos
        v = _struct.unpack_from(">Q", datos, pos)[0]; pos += 8; return v
    claves = []
    for _ in range(i32()):
        n = i32(); claves.append(datos[pos:pos + n]); pos += n
    valores = [(u64(), i32()) for _ in range(i32())]
    subnodos = [u64() for _ in range(17)]
    return claves, valores, subnodos


async def _ids_de_palabra(s, version: str, palabra: str) -> set:
    base = f"{LTN}/galleriesindex/galleries.{version}"
    clave = _hashlib.sha256(palabra.encode()).digest()[:4]
    direccion = 0
    for _ in range(40):  # profundidad máxima de seguridad
        claves, valores, subnodos = _nodo(await _rango(s, base + ".index", direccion, direccion + TAM_NODO - 1))
        i = 0
        while i < len(claves) and clave > claves[i]:
            i += 1
        if i < len(claves) and clave == claves[i]:
            offset, largo = valores[i]
            if largo <= 0 or largo > 100_000_000:
                return set()
            datos = await _rango(s, base + ".data", offset, offset + largo - 1)
            n = _struct.unpack_from(">i", datos, 0)[0]
            return set(_struct.unpack_from(f">{n}i", datos, 4)) if n * 4 + 4 == len(datos) else set()
        if not any(subnodos) or subnodos[i] == 0:
            return set()
        direccion = subnodos[i]
    return set()


async def _ids_nozomi(s, ruta: str) -> set:
    async with s.get(f"{LTN}/{ruta}", headers={"Referer": "https://hitomi.la/"}) as r:
        if r.status != 200:
            return set()
        datos = await r.read()
    return set(_struct.unpack(f">{len(datos) // 4}i", datos[: len(datos) // 4 * 4]))


async def buscar_ids(texto: str, s: aiohttp.ClientSession) -> List[int]:
    """Ids de galerías de Hitomi para un texto: palabras del título (intersección) y, si el texto
    entero es un artista o grupo, también sus obras. Más recientes primero."""
    palabras = [w for w in re.findall(r"[a-z0-9]+", texto.lower()) if len(w) > 1]
    if not palabras:
        return []
    async with s.get(f"{LTN}/galleriesindex/version?_={int(_time.time())}",
                     headers={"Referer": "https://hitomi.la/"}) as r:
        version = (await r.text()).strip()
    if not version.isdigit():
        raise ErrorResolucion("versión del índice de Hitomi no válida")
    nombre = " ".join(palabras)
    titulo_sets, artista, grupo = await _asyncio.gather(
        _asyncio.gather(*(_ids_de_palabra(s, version, w) for w in palabras)),
        _ids_nozomi(s, f"artist/{nombre}-all.nozomi"),
        _ids_nozomi(s, f"group/{nombre}-all.nozomi"),
    )
    por_titulo = set.intersection(*titulo_sets) if titulo_sets and all(titulo_sets) else set()
    return sorted(por_titulo | artista | grupo, reverse=True)


async def info_galeria(gid: int, s: aiohttp.ClientSession, reglas: Optional["ReglasGG"] = None):
    """(título, url, portada, artistas, idioma, nº de páginas) de una galería de Hitomi.
    None si la galería no existe (404). Si tiene la etiqueta "uncensored" y el título no lo dice,
    se le añade " (Uncensored)" (para mostrarlo y para la búsqueda de la versión sin censura)."""
    async with s.get(f"{LTN}/galleries/{gid}.js", headers={"Referer": "https://hitomi.la/"}) as r:
        if r.status != 200:
            return None
        texto = await r.text()
    info = json.loads(texto[texto.find("{"):])
    titulo = info.get("title") or f"Hitomi {gid}"
    etiquetas = {str(t.get("tag", "")).lower() for t in info.get("tags") or [] if isinstance(t, dict)}
    if "uncensored" in etiquetas and not re.search(r"uncensor|decensor", titulo, re.I):
        titulo += " (Uncensored)"
    tipo = info.get("type") or "doujinshi"
    portada = ""
    archivos = info.get("files") or []
    if archivos and reglas:
        h = archivos[0]["hash"]
        g = int(h[-1] + h[-3:-1], 16)
        # Miniatura grande: common.js url_from_url_from_hash(id, img, "webpbigtn", "webp", "tn")
        portada = f"https://{chr(97 + reglas.m(g))}tn.{DOMINIO}/webpbigtn/{h[-1]}/{h[-3:-1]}/{h}.webp"
    artistas = [a.get("artist") for a in info.get("artists") or [] if a.get("artist")]
    return (titulo, f"https://hitomi.la/{tipo}/{gid}.html", portada, artistas, (info.get("language") or "").lower(),
            len(archivos))


async def reglas_gg(s: aiohttp.ClientSession) -> Optional["ReglasGG"]:
    try:
        async with s.get(f"{LTN}/gg.js", headers={"Referer": "https://hitomi.la/"}) as r:
            return ReglasGG.desde_js(await r.text())
    except Exception:
        return None
