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
  hentai2read.com   /search?q=<texto>  (Inertia.js: JSON en <script data-page="app">)
  m440.in           /search?q=<texto>  (JSON)
  hentalk.pw        /api/library?q=<texto>  (JSON; SpyFakku, antes también fakku.cc)
  imhentai.xxx      /search/?key=<texto>
  allporncomic.com  /?s=<texto>&post_type=wp-manga  (Madara; resultado = cómic, se descarga por capítulos)
  sexkomix2.com     /search/?lang=en&q=<texto>  (y lang=es; href con espacios alrededor del "=")
  hitomi.la         índices binarios (árbol B de palabras del título + ficheros .nozomi de artista/grupo)
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
    idioma: Optional[str] = None  # "es", "en", "ja", "zh"... (None = desconocido)
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
# hentai2read casa por título alternativo ("natsuzuma" -> "Summer Wife ..."): sí se rescata.
# 3hentai NO es estricto: con una búsqueda sin coincidencias devuelve igualmente 25 galerías
# (comprobado con "zzqqxxnotexist"), que se colaban como si casaran por artista.
ESTRICTOS = {"hitomi", "hentainexus", "nhentai.to", "nhentai.xxx", "nhentai.website", "hentai2read", "m440",
             "hentalk", "imhentai", "allporncomic"}
PUNTUACION_POR_ETIQUETA = 0.6


# ─── Extracción de enlaces de resultados ─────────────────────────────────────
def _limpio(t: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t))).strip()


def _enlaces(pagina: str, base: str, patron: str) -> List[Tuple[str, str, str]]:
    """[(url absoluta, título, portada)] de los <a> cuyo href casa con patron. Título: atributo
    title, alt de la imagen, o el texto del enlace (el más largo si un mismo enlace aparece varias
    veces). Portada: la primera imagen dentro del enlace (data-src antes que src; sin data:)."""
    vistos: dict = {}
    portada_sola: dict = {}  # enlaces que solo traen la imagen (imhentai: <a><img></a> y luego <a>Título</a>)
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
        t = html.unescape(t)  # algunos sitios escapan dos veces (&amp;#039;)
        if not t and portada:
            portada_sola.setdefault(clave, portada)
        portada = portada or portada_sola.get(clave, "")
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


async def _hitomi(s, q):
    """Hitomi: índices binarios (ver core/sites/hitomi_http.py). Datos de las 20 más recientes."""
    from .sites import hitomi_http
    ids = (await hitomi_http.buscar_ids(q, s))[:20]
    if not ids:
        return []
    reglas = await hitomi_http.reglas_gg(s)
    sem = asyncio.Semaphore(8)

    async def una(gid):
        async with sem:
            try:
                return await hitomi_http.info_galeria(gid, s, reglas)
            except Exception:
                return None
    infos = await asyncio.gather(*(una(g) for g in ids))
    return [("hitomi", t, u, img, idioma) for t, u, img, _, idioma, _ in (i for i in infos if i)]


async def _hentai2read(s, q):
    """Inertia.js: los resultados van en JSON (props.books.data) dentro de la página."""
    from .sites.h2r import datos_pagina
    p = await _get(s, f"https://hentai2read.com/search?q={quote_plus(q)}")
    libros = ((datos_pagina(p).get("props") or {}).get("books") or {}).get("data") or []
    return [("hentai2read", b.get("title", ""), urljoin("https://hentai2read.com/", b.get("url", "")), b.get("cover", ""))
            for b in libros if b.get("title") and b.get("url")]


async def _m440(s, q):
    """m440.in: /search?q= devuelve JSON [{value: título, label: html con enlace y portada}]."""
    import json
    texto = await _get(s, f"https://m440.in/search?q={quote_plus(q)}")
    try:
        datos = json.loads(texto) if texto else []
    except ValueError:
        return []
    res = []
    for d in datos if isinstance(datos, list) else []:
        etiqueta = d.get("label", "")
        u = re.search(r'href="([^"]+/manga/[^"]+)"', etiqueta)
        img = re.search(r'src="([^"]+)"', etiqueta)
        if d.get("value") and u:
            res.append(("m440", html.unescape(d["value"]), u.group(1), img.group(1) if img else ""))
    return res


async def _hentalk(s, q):
    """hentalk.pw (SpyFakku, catálogo de FAKKU en inglés): API JSON /api/library?q=<texto>."""
    import json
    texto = await _get(s, f"https://hentalk.pw/api/library?q={quote_plus(q)}",
                       headers={"X-Requested-With": "XMLHttpRequest"})
    try:
        archivos = (json.loads(texto) if texto else {}).get("archives") or []
    except ValueError:
        return []
    return [("hentalk", a.get("title", ""), f"https://hentalk.pw/g/{a['id']}",
             f"https://hentalk.pw/image/{a['hash']}/{a.get('thumbnail', 1)}?type=cover" if a.get("hash") else "")
            for a in archivos if a.get("id") and a.get("title")]


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


async def _imhentai(s, q):
    p = await _get(s, f"https://imhentai.xxx/search/?key={quote_plus(q)}")
    return [("imhentai", t, u, img) for u, t, img in _enlaces(p, "https://imhentai.xxx/", r"/gallery/\d+/")]


async def _allporncomic(s, q):
    p = await _get(s, f"https://allporncomic.com/?s={quote_plus(q)}&post_type=wp-manga")
    # Tarjeta Madara: <a href=".../porncomic/<slug>/" title="..."><img data-src="portada"></a> ...
    #                 <h3 class="h4"><a href=".../porncomic/<slug>/">Título</a></h3>
    portadas = dict(re.findall(r'<a href="(https://allporncomic\.com/porncomic/[^"/?]+/)"[^>]*>\s*<img[^>]*'
                               r'data-src="([^"]+)"', p))
    return [("allporncomic", _limpio(t), u, portadas.get(u, ""))
            for u, t in re.findall(r'<h3 class="h4">\s*<a href="(https://allporncomic\.com/porncomic/[^"/?]+/)">'
                                   r'(.*?)</a>', p, re.S)]


async def _sexkomix2(s, q):
    res = []
    for lang in ("es", "en"):
        p = await _get(s, f"https://sexkomix2.com/search/?lang={lang}&q={quote_plus(q)}")
        for m in re.finditer(r'<li class\s*=\s*"comix">\s*<a href\s*=\s*"([^"]+)"(.*?)</a>', p, re.S):
            href, dentro = m.group(1).strip(), m.group(2)
            if not re.search(r"/comicsx_[a-z]{2}/\d+", href):
                continue
            t = re.search(r'class\s*=\s*"comix_title">\s*<h2><p>(.*?)</p>', dentro, re.S)
            img = re.search(r'data-src\s*=\s*"([^"]+)"', dentro)
            titulo = _limpio(t.group(1)) if t else ""
            titulo = re.sub(r"^(?:Porn comic|Cómic porno|Comic porno)\s+", "", titulo, flags=re.I)
            if titulo:
                res.append(("sexkomix2", titulo, urljoin("https://sexkomix2.com/", href), img.group(1) if img else "",
                            "spanish" if lang == "es" else "english"))
    return res



BUSCADORES = [_hitomi, _hentainexus, _nhentai_to, _nhentai_xxx, _nhentai_website, _3hentai, _hentai2read, _m440,
              _hentalk, _tmohentai, _lectorhentai, _imhentai, _allporncomic, _sexkomix2]


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
        for sitio, titulo, url, portada, *pista in lista:
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
                resultados.append(Resultado(sitio, titulo, url, p, titulo_base(titulo)[1], portada,
                                            idioma_de(titulo, sitio, pista[0] if pista else "")))
    resultados.sort(key=lambda r: (-r.puntuacion, r.numero if r.numero is not None else 1e9, r.sitio))
    return resultados[:limite]


# ─── Búsqueda por código ("nuclear code") ────────────────────────────────────
# Un número a secas ("177013", "#177013") es el código de una galería. El MISMO número es una
# galería DISTINTA en cada sitio, así que se devuelve una tarjeta por sitio, sin mezclarlas.
#  - nhentai: nhentai.net es una app JS tras Cloudflare (no se usa). De sus mirrors, solo
#    nhentai.to conserva la numeración de nhentai (visto el 26/11/2026: 177013 = METAMORPHOSIS,
#    hasta ~700000). nhentai.xxx, nhentai.website y 3hentai numeran a su manera: no sirven.
#  - hitomi.la: ltn.<dominio>/galleries/<id>.js (el mismo JSON que usa el handler).
_CODIGO = re.compile(r"^\s*(?:#|n[º°o]\.?\s*)?\s*(\d{1,7})\s*$", re.I)
_IDIOMAS_NO = {"translated", "rewrite", "speechless", "text cleaned"}


def codigo_de(texto: str) -> Optional[int]:
    """'177013' / '#177013' -> 177013. None si el texto no es solo un número."""
    m = _CODIGO.match(texto or "")
    return int(m.group(1)) if m and int(m.group(1)) > 0 else None


async def _codigo_nhentai(s: aiohttp.ClientSession, gid: int) -> Optional[Resultado]:
    url = f"https://nhentai.to/g/{gid}"
    async with s.get(url, headers={"Referer": "https://nhentai.to/"}) as r:
        if r.status != 200:
            return None  # 404: no existe (o el mirror está caído)
        p = await r.text(errors="replace")
    m = re.search(r"<h1[^>]*>(.*?)</h1>", p, re.S | re.I)
    titulo = _limpio(m.group(1)) if m else ""
    if not titulo:
        return None
    portada = ""
    m = re.search(r'<div id="cover">.*?<img[^>]*?\bsrc="([^"]+)"', p, re.S)
    if m:
        portada = urljoin(url, html.unescape(m.group(1)))
    idiomas = [x for x in re.findall(r'href="/language/([^/"]+)/"', p) if x.replace("-", " ") not in _IDIOMAS_NO]
    artistas = list(dict.fromkeys(_limpio(re.sub(r"<span class=\"count\">.*?</span>", "", a))
                                  for a in re.findall(r'href="/artist/[^"]+"[^>]*>(.*?)</a>', p, re.S)))
    artistas = [re.sub(r"\s*\d+(?:\.\d+)?[kKmM]?\s*$", "", a) for a in artistas if a]
    m = re.search(r'"num_pages":\s*(\d+)', p) or re.search(r"Pages:\s*(?:<[^>]+>\s*)*(\d+)", p)
    paginas = int(m.group(1)) if m else None
    return Resultado("nhentai.to", titulo, url, 1.0, None, portada,
                     idioma_de(titulo, "nhentai.to", idiomas[0] if idiomas else ""),
                     {"codigo": gid, "artistas": artistas[:3], "paginas": paginas})


async def _codigo_hitomi(s: aiohttp.ClientSession, gid: int) -> Optional[Resultado]:
    from .sites import hitomi_http
    reglas = await hitomi_http.reglas_gg(s)
    info = await hitomi_http.info_galeria(gid, s, reglas)
    if not info:
        return None
    titulo, url, portada, artistas, idioma, paginas = info
    return Resultado("hitomi", titulo, url, 1.0, None, portada, idioma_de(titulo, "hitomi", idioma),
                     {"codigo": gid, "artistas": artistas[:3], "paginas": paginas})


async def buscar_codigo(gid: int) -> List[Resultado]:
    """La galería con ese código en nhentai (vía nhentai.to) y en Hitomi, una por sitio.
    Un sitio donde no existe (o que no responde) simplemente no aparece."""
    async with aiohttp.ClientSession(headers={"User-Agent": config.USER_AGENT}, timeout=TIMEOUT) as s:
        async def seguro(f):
            try:
                return await f(s, gid)
            except Exception:
                return None
        res = await asyncio.gather(seguro(_codigo_nhentai), seguro(_codigo_hitomi))
    return [r for r in res if r]


# ─── Idioma ──────────────────────────────────────────────────────────────────
_IDIOMAS_TITULO = [  # (código, patrón en el título); el primero que aparezca manda
    ("es", r"espa[ñn]ol|spanish|castellano|spa|esp"),
    ("en", r"english|eng"),
    ("zh", r"chinese|中文|汉化|漢化|中国翻訳|中國翻譯"),
    ("ko", r"korean|한국어|kor"),
    ("vi", r"vietnamese|ti[ếe]ng vi[ệe]t"),
    ("ru", r"russian|русский"),
    ("pt", r"portugu[eê]s|portuguese|pt-?br"),
    ("fr", r"fran[cç]ais|french"),
    ("id", r"indonesian|bahasa"),
    ("th", r"thai"),
    ("de", r"german|deutsch"),
    ("it", r"italian|italiano"),
    ("ja", r"japanese|日本語|raw"),
]
_IDIOMA_HITOMI = {"spanish": "es", "english": "en", "japanese": "ja", "chinese": "zh", "korean": "ko",
                  "vietnamese": "vi", "russian": "ru", "portuguese": "pt", "french": "fr", "indonesian": "id",
                  "thai": "th", "german": "de", "italian": "it"}
# Sitios que solo publican en un idioma
_IDIOMA_SITIO = {"tmohentai.app": "es", "lectorhentai": "es", "m440": "es", "hentainexus": "en", "hentai2read": "en",
                 "hentalk": "en", "allporncomic": "en"}

BANDERA = {"es": "🇪🇸", "en": "🇬🇧", "ja": "🇯🇵", "zh": "🇨🇳", "ko": "🇰🇷", "vi": "🇻🇳", "ru": "🇷🇺", "pt": "🇧🇷",
           "fr": "🇫🇷", "id": "🇮🇩", "th": "🇹🇭", "de": "🇩🇪", "it": "🇮🇹", None: "🏳️"}
# Orden de presentación: español, inglés, desconocido, japonés (original) y el resto
_PRIORIDAD = {"es": 0, "en": 1, None: 2, "ja": 3}


def idioma_de(titulo: str, sitio: str, pista: str = "") -> Optional[str]:
    """Idioma de un resultado: dato del sitio (Hitomi), etiqueta del título o idioma fijo del sitio.
    Un título solo con kana/kanji y sin etiqueta se toma como japonés."""
    if pista and pista.lower() in _IDIOMA_HITOMI:
        return _IDIOMA_HITOMI[pista.lower()]
    # Las etiquetas de idioma van entre corchetes o paréntesis; si no hay, se mira el título entero
    etiquetas = " ".join(re.findall(r"[\[(]([^\])]+)[\])]", titulo or "")) or (titulo or "")
    for codigo, patron in _IDIOMAS_TITULO:
        if re.search(patron, etiquetas, re.I) or re.search(patron, titulo or "", re.I):
            return codigo
    if sitio in _IDIOMA_SITIO:
        return _IDIOMA_SITIO[sitio]
    if re.search(r"[가-힯]", titulo or ""):  # hangul
        return "ko"
    if re.search(r"[぀-ヿ一-鿿]", titulo or "") and not re.search(r"[a-zA-Z]{4,}", titulo or ""):
        return "ja"
    return None


def prioridad_idioma(r: "Resultado") -> int:
    return _PRIORIDAD.get(r.idioma, 4)


def ordenar_por_idioma(resultados: List["Resultado"]) -> List["Resultado"]:
    """Español primero, luego inglés, desconocido, japonés y el resto; dentro, por parecido."""
    return sorted(resultados, key=lambda r: (prioridad_idioma(r), -r.puntuacion,
                                             r.numero if r.numero is not None else 1e9, r.sitio))


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
    if "hentai2read" in host:
        from .sites.h2r import datos_pagina
        libro = (datos_pagina(p).get("props") or {}).get("book") or {}
        for g in libro.get("groups") or []:
            if g.get("type") == "artist":
                nombres = [t.get("name") for t in g.get("tags") or [] if t.get("name")]
                if nombres:
                    return ", ".join(nombres[:3])
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
