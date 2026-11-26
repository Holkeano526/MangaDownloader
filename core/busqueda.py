"""
Búsqueda en todos los sitios compatibles, autor de una galería y continuaciones.

  buscar(texto)           -> resultados de todos los sitios en paralelo, ordenados por parecido
  autor(url, titulo)      -> autor/artista de una galería (enlace de artista o "[Círculo (Artista)]")
  continuaciones(titulo)  -> otras partes de la misma serie (número mayor) y obras relacionadas

Búsqueda por sitio (vista el 25/11/2026; todo por HTTP):
  hentainexus.com   /?q=<texto>
  nhentai.to        /search/?q=<texto>
  nhentai.xxx       /search/?key=<texto>
  nhentai.website   /search?q=<texto>
  tmohentai.app     /?title=<texto> con X-Requested-With (devuelve JSON {"html": ...})
  lectorhentai.com  /tipo/all?s=<texto>
  3hentai.net       /search?q=<texto>
Hitomi no tiene un buscador por HTTP sencillo (índices binarios "nozomi"): no se busca allí.
"""
from __future__ import annotations

import asyncio
import html
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple
from urllib.parse import quote_plus, urljoin, urlparse

import aiohttp

from . import config

TIMEOUT = aiohttp.ClientTimeout(total=20, connect=8)


@dataclass
class Resultado:
    sitio: str
    titulo: str
    url: str
    puntuacion: float = 0.0
    numero: Optional[float] = None
    portada: str = ""  # URL de la miniatura/portada (la tarjeta del resultado la trae)
    extra: dict = field(default_factory=dict)


# ─── Normalización de títulos ────────────────────────────────────────────────
_RUIDO = re.compile(r"\[[^\]]*\]|\{[^}]*\}|\((?:sin censura|uncensored|decensored|english|español|spanish|"
                    r"digital|color|full color|[^)]*traducci[oó]n[^)]*)\)", re.I)


def normalizar(t: str) -> str:
    t = unicodedata.normalize("NFKD", html.unescape(t or "")).encode("ascii", "ignore").decode()
    t = _RUIDO.sub(" ", t)
    t = re.sub(r"[^a-z0-9]+", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


_PARTE = re.compile(r"\s+(?:part|parte|vol(?:ume|umen)?|ch(?:apter)?|cap(?:itulo)?|ep(?:isode)?|#)?\s*"
                    r"(\d+(?:\.\d+)?)(?:\s*(?:-|~)\s*\d+)?\s*$", re.I)


def titulo_base(t: str) -> Tuple[str, Optional[float]]:
    """'The Wife-Lenders 1' -> ('the wife lenders', 1). Sin número -> (título, None)."""
    n = normalizar(t)
    m = _PARTE.search(n)
    if m and m.start() > 0:
        return n[:m.start()].strip(), float(m.group(1))
    return n, None


# Palabras que no distinguen nada al comparar títulos (inglés, español, romaji)
VACIAS = {"the", "a", "an", "of", "and", "to", "in", "on", "with", "for", "my", "is", "el", "la", "los", "las",
          "de", "del", "y", "en", "un", "una", "mi", "con", "no", "wo", "ga", "ni", "wa", "to", "na", "o"}


def parecido(consulta: str, titulo: str) -> float:
    """Fracción de palabras significativas de la consulta presentes en el título, con bonus si
    aparecen seguidas. Las palabras vacías ("the", "de", "no"...) no cuentan."""
    todas = normalizar(consulta).split()
    q = [w for w in todas if w not in VACIAS] or todas
    if not q:
        return 0.0
    mejor = 0.0
    # Se compara con el título limpio y con el título COMPLETO: entre corchetes van el círculo
    # y el artista ("[Hanamori Banbi (Babi)] ..."), y se busca por ellos ("!buscar babi").
    for t in (normalizar(titulo), _normalizar_completo(titulo)):
        palabras = set(t.split())
        base = sum(1 for w in q if w in palabras) / len(q)
        mejor = max(mejor, base + (0.25 if " ".join(todas) in t else 0.0))
    return mejor


def _normalizar_completo(t: str) -> str:
    """Como normalizar() pero conservando lo que hay entre corchetes y paréntesis."""
    t = unicodedata.normalize("NFKD", html.unescape(t or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", t.lower())).strip()


# Sitios cuyo buscador es estricto (casa título, artista o etiqueta exactos): si un resultado
# suyo no contiene las palabras en el título, es que casó por artista/etiqueta y se conserva
# (por detrás de los que sí casan por título). lectorhentai busca palabra a palabra y tmohentai
# casa también palabras traducidas ("wife" -> "Mi Esposa Gyaru"): meten ruido y no se rescatan.
ESTRICTOS = {"hentainexus", "nhentai.to", "nhentai.xxx", "nhentai.website", "3hentai"}
PUNTUACION_POR_ETIQUETA = 0.6


# ─── Extracción de enlaces de resultados ─────────────────────────────────────
def _limpio(t: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t))).strip()


def _enlaces(pagina: str, base: str, patron: str) -> List[Tuple[str, str, str]]:
    """[(url absoluta, título, portada)] de los <a> cuyo href casa con patron. Título: atributo
    title, alt de la imagen, o el texto del enlace (el más largo si un mismo enlace aparece varias
    veces). Portada: la primera imagen dentro del enlace (data-src antes que src; sin data:)."""
    vistos: dict = {}
    for m in re.finditer(rf'<a\b([^>]*href="([^"]*{patron}[^"]*)"[^>]*)>(.*?)</a>', pagina, re.S | re.I):
        attrs, href, dentro = m.group(1), m.group(2), m.group(3)
        t = ""
        a = re.search(r'\btitle="([^"]+)"', attrs)
        if a:
            t = html.unescape(a.group(1))
        if not t:
            t = _limpio(dentro)
        if not t:
            a = re.search(r'\balt="([^"]+)"', dentro)
            t = html.unescape(a.group(1)) if a else ""
        clave = re.search(patron, href).group(0)
        portada = ""
        for img in re.findall(r'(?:data-src|data-lazy-src|src)="([^"]+)"', dentro):
            if not img.startswith("data:"):
                portada = urljoin(base, img)
                break
        previo = vistos.get(clave)
        if t and (previo is None or len(t) > len(previo[1])):
            vistos[clave] = (urljoin(base, href), t.strip(), portada or (previo[2] if previo else ""))
        elif previo and not previo[2] and portada:
            vistos[clave] = (previo[0], previo[1], portada)
    return list(vistos.values())


# ─── Buscadores por sitio ────────────────────────────────────────────────────
async def _get(s: aiohttp.ClientSession, url: str, **kw) -> str:
    async with s.get(url, **kw) as r:
        if r.status != 200:
            return ""
        return await r.text(errors="replace")


async def _hentainexus(s, q):
    p = await _get(s, f"https://hentainexus.com/?q={quote_plus(q)}")
    return [("hentainexus", t, u, img) for u, t, img in _enlaces(p, "https://hentainexus.com/", r"/view/\d+")]


async def _nhentai_to(s, q):
    p = await _get(s, f"https://nhentai.to/search/?q={quote_plus(q)}")
    return [("nhentai.to", t, u, img) for u, t, img in _enlaces(p, "https://nhentai.to/", r"/g/\d+")]


async def _nhentai_xxx(s, q):
    p = await _get(s, f"https://nhentai.xxx/search/?key={quote_plus(q)}")
    return [("nhentai.xxx", t, u, img) for u, t, img in _enlaces(p, "https://nhentai.xxx/", r"/g/\d+")]


async def _nhentai_website(s, q):
    p = await _get(s, f"https://nhentai.website/search?q={quote_plus(q)}")
    return [("nhentai.website", t, u, img) for u, t, img in _enlaces(p, "https://nhentai.website/", r"/read/\d+")]


async def _3hentai(s, q):
    p = await _get(s, f"https://3hentai.net/search?q={quote_plus(q)}")
    return [("3hentai", t, u, img) for u, t, img in _enlaces(p, "https://3hentai.net/", r"/d/\d+")]


async def _tmohentai(s, q):
    import json
    cab = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}
    texto = await _get(s, f"https://tmohentai.app/?title={quote_plus(q)}", headers=cab)
    try:
        p = json.loads(texto).get("html", "") if texto else ""
    except ValueError:
        p = ""
    return [("tmohentai.app", t, u, img) for u, t, img in _enlaces(p, "https://tmohentai.app/", r"/library/[a-z]+/\d+")]


async def _lectorhentai(s, q):
    p = await _get(s, f"https://lectorhentai.com/tipo/all?s={quote_plus(q)}")
    res = []
    for u, t, img in _enlaces(p, "https://lectorhentai.com/", r"/manga/\d+"):
        # El texto de la tarjeta lleva tipo delante y fecha detrás: "Hentai Sin Censura <título> hace 3 días"
        t = re.sub(r"^(?:Hentai|Manga|Doujinshi)(?:\s+Sin Censura)?\s+", "", t, flags=re.I)
        t = re.sub(r"\s+(?:hace\s+\d+\s+\w+|\d{1,2}\s+\w{3}\s+\d{4})\s*$", "", t, flags=re.I)
        res.append(("lectorhentai", t, u, img))
    return res


BUSCADORES = [_hentainexus, _nhentai_to, _nhentai_xxx, _nhentai_website, _3hentai, _tmohentai, _lectorhentai]


async def buscar(texto: str, minimo: float = 0.6, limite: int = 25) -> List[Resultado]:
    """Busca en todos los sitios a la vez. Un sitio caído o lento no bloquea al resto."""
    # Variantes de la consulta: tal cual, sin puntuación y solo con las palabras significativas.
    # Cada sitio falla con una distinta: nhentai.to no devuelve nada si la búsqueda lleva "The"
    # ("The Wife Lenders" -> 0, "wife lenders" -> 1).
    simple = re.sub(r"\s+", " ", re.sub(r"[^\w\s]+", " ", texto)).strip()
    clave = " ".join(w for w in simple.split() if w.lower() not in VACIAS)
    variantes = []
    for v in (texto, simple, clave):
        if v and v.lower() not in (x.lower() for x in variantes):
            variantes.append(v)
    async with aiohttp.ClientSession(headers={"User-Agent": config.USER_AGENT}, timeout=TIMEOUT) as s:
        async def seguro(f, q):
            try:
                return await f(s, q)
            except Exception:
                return []
        listas = await asyncio.gather(*(seguro(f, q) for f in BUSCADORES for q in variantes))
    resultados, urls = [], set()
    for lista in listas:
        for sitio, titulo, url, portada in lista:
            if url in urls:
                continue
            p = parecido(texto, titulo)
            if p == 0 and sitio in ESTRICTOS:
                # Ninguna palabra en el título: el sitio lo encontró por artista o etiqueta.
                # (Si casa ALGUNA palabra es una coincidencia parcial de título, p. ej. "The Demon
                # Wife" para "The Wife-Lenders", y no se rescata.)
                p = PUNTUACION_POR_ETIQUETA
            if p >= minimo:
                urls.add(url)
                resultados.append(Resultado(sitio, titulo, url, p, titulo_base(titulo)[1], portada))
    resultados.sort(key=lambda r: (-r.puntuacion, r.numero if r.numero is not None else 1e9, r.sitio))
    return resultados[:limite]


# ─── Autor ───────────────────────────────────────────────────────────────────
def autor_desde_titulo(titulo: str) -> Optional[str]:
    """Convención de los doujinshi: '[Círculo (Artista)] Título' o '[Artista] Título'."""
    m = re.search(r"\[([^\]\[]+?)\s*\(([^)]+)\)\s*\]", titulo or "")
    if m:
        return m.group(2).strip()
    m = re.match(r"\s*\[([^\]]+)\]", titulo or "")
    if m and not re.search(r"english|español|spanish|digital|decensored|chinese", m.group(1), re.I):
        return m.group(1).strip()
    return None


def _autor_en_pagina(url: str, p: str) -> Optional[str]:
    host = urlparse(url).netloc
    if "hentainexus" in host:
        m = re.search(r"Artist</td>\s*<td[^>]*>(.*?)</td>", p, re.S | re.I)
        if m:
            return re.sub(r"\s*\(\d+\)\s*$", "", _limpio(m.group(1))) or None
    if "lectorhentai" in host:
        m = re.search(r"<b>Artista</b>\s*<span[^>]*>(.*?)</span>", p, re.S | re.I)
        if m:
            return _limpio(m.group(1)) or None
    # Familia nhentai: enlaces de etiqueta de artista (/artist/<slug>/ o /browse/artist/<slug>)
    nombres = []
    for m in re.finditer(r'href="[^"]*/(?:browse/)?artists?/([^/"]+)/?"[^>]*>(.*?)</a>', p, re.S | re.I):
        texto = re.sub(r"\s*\d+(?:\.\d+)?[kKmM]?\s*$", "", _limpio(m.group(2)))  # quita el contador
        nombre = texto or m.group(1).replace("-", " ")
        if nombre and nombre.lower() not in ("artists", "artist") and nombre not in nombres:
            nombres.append(nombre)
    return ", ".join(nombres[:3]) if nombres else None


async def autor(url: str, titulo: str = "") -> Optional[str]:
    """Autor de una galería: de su página si el sitio lo muestra; si no, de la convención del título."""
    if "hitomi.la" in url:
        m = re.search(r"[-/](\d+)\.html", url)
        if m:
            try:
                async with aiohttp.ClientSession(headers={"User-Agent": config.USER_AGENT,
                                                          "Referer": "https://hitomi.la/"}, timeout=TIMEOUT) as s:
                    t = await _get(s, f"https://ltn.gold-usergeneratedcontent.net/galleries/{m.group(1)}.js")
                import json
                info = json.loads(t[t.find("{"):]) if t else {}
                artistas = [a.get("artist") for a in info.get("artists") or [] if a.get("artist")]
                if artistas:
                    return ", ".join(artistas[:3])
                titulo = titulo or info.get("title", "")
            except Exception:
                pass
    else:
        try:
            async with aiohttp.ClientSession(headers={"User-Agent": config.USER_AGENT}, timeout=TIMEOUT) as s:
                a = _autor_en_pagina(url, await _get(s, url))
            if a:
                return a
        except Exception:
            pass
    return autor_desde_titulo(titulo)


# ─── Continuaciones ──────────────────────────────────────────────────────────
async def continuaciones(titulo: str, url_actual: str = "") -> Tuple[List[Resultado], List[Resultado]]:
    """(partes siguientes, relacionadas). Busca el título sin su número en todos los sitios.
    Partes siguientes: mismo título base con número mayor. Relacionadas: empiezan igual sin número
    (p. ej. "The Wife-Lenders: Emi's Turn")."""
    base, numero = titulo_base(titulo)
    if len(base) < 3:
        return [], []
    # Para buscar, el título base legible (sin el número final)
    consulta = re.sub(r"[\s:,.\-]*(?:part|parte|vol\.?|#)?\s*\d+(?:\.\d+)?\s*$", "", titulo.strip(), flags=re.I) or base
    consulta = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", consulta).strip() or base
    resultados = await buscar(consulta, minimo=0.8, limite=60)
    siguientes, relacionadas, vistos = [], [], set()
    for r in resultados:
        if url_actual and r.url.rstrip("/") == url_actual.rstrip("/"):
            continue
        rb, rn = titulo_base(r.titulo)
        if base not in rb:
            continue
        if rn is None:
            # Número justo detrás del título base: "Natsuzuma 2 Natsu Ryokan..." -> parte 2
            m = re.search(rf"(?:^|\s){re.escape(base)}\s+(\d+(?:\.\d+)?)(?:\s|$)", rb)
            if m:
                rn = float(m.group(1))
                r.numero = rn
        if rn is not None and numero is not None and rn <= numero:
            continue  # partes anteriores o la misma
        clave = (rb, rn)
        if rn is not None and (numero is None or rn > numero):
            if clave not in vistos:
                vistos.add(clave)
                siguientes.append(r)
        elif rn is None and rb != base:
            if clave not in vistos:
                vistos.add(clave)
                relacionadas.append(r)
    siguientes.sort(key=lambda r: (r.numero or 0, r.sitio))
    return siguientes[:10], relacionadas[:5]
