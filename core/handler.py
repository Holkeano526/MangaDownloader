"""
Module: core.handler
Description: Acts as the central router for the Manga Downloader application.
It parses incoming URLs and delegates the scraping/downloading process to
the appropriate site-specific handler (e.g., TMO, Hitomi, NHentai).
"""

from typing import Callable, Optional
from .sites import (
    TMOHentaiAppHandler,
    LectorHentaiHandler,
    HentaiNexusHandler,
    HitomiHandler,
    NHentaiMirrorHandler,
    H2RHandler,
    M440Handler,
    HentalkHandler,
    ShadeMangaHandler,
    SexKomix2Handler,
    AllPornComicHandler,
    IMHentaiHandler,
    EromeHandler,
    BunkrHandler,
    NotFansHandler,
    FapslyHandler,
    TuboHandler,
    WPVideoHandler,
)
from .sites.tubos import TUBOS
from .sites.wpvideos import WORDPRESS

# Instantiate handlers (stateless). Gana el primero cuyo dominio aparezca en la URL.
HANDLERS = [
    TMOHentaiAppHandler(),
    LectorHentaiHandler(),
    HentaiNexusHandler(),
    HitomiHandler(),
    IMHentaiHandler(),       # antes que los mirrors de nhentai por si algún dominio se parece
    NHentaiMirrorHandler(),  # nhentai.* (to, xxx, website, net, top...) y 3hentai.net
    H2RHandler(),
    M440Handler(),
    HentalkHandler(),        # hentalk.pw y los enlaces viejos de fakku.cc (mismo servidor)
    ShadeMangaHandler(),
    SexKomix2Handler(),
    AllPornComicHandler(),
    EromeHandler(),
    BunkrHandler(),
    NotFansHandler(),
    FapslyHandler(),
] + [TuboHandler(cfg) for cfg in TUBOS] + [WPVideoHandler(cfg) for cfg in WORDPRESS]

from urllib.parse import urlparse

async def process_entry(
    url: str,
    log_callback: Callable[[str], None],
    check_cancel: Callable[[], bool],
    progress_callback: Optional[Callable[[int, int], None]] = None
) -> list[str] | None:
    """
    Main Router: Redirects to the specific site handler based on the URL.
    
    Args:
        url (str): The manga chapter/gallery URL submitted by the user.
        log_callback (Callable): Function to emit asynchronous log messages to the frontend.
        check_cancel (Callable): Function returning a boolean to determine if the user cancelled the process.
        progress_callback (Optional[Callable]): Function to emit progress updates (current, total) to the frontend.
    
    Returns:
        None: Side-effect function that drives the download processing.
    """
    hostname = host_de_url(url)
    if not hostname:
        log_callback("[ERROR] Invalid URL provided (solo se admiten enlaces http/https a un dominio).")
        return

    for handler in HANDLERS:
        # Prevención de SSRF: se compara el hostname real (urlparse().hostname, sin usuario ni puerto)
        # por dominio completo o subdominio. Antes se buscaba el texto en netloc, y
        # "https://sexkomix2.com@192.168.1.1/" o "nhentai.10.0.0.5.nip.io" pasaban el filtro.
        if any(dominio_coincide(hostname, d) for d in handler.get_supported_domains()):
            return await handler.process(url, log_callback, check_cancel, progress_callback)

    if any(dominio_coincide(hostname, d) for d in BLOQUEADOS_CLOUDFLARE):
        log_callback(f"[ERROR] {hostname} está protegido por Cloudflare (desafío anti-bots en todas sus páginas) "
                     f"y no se puede descargar automáticamente. Busca la obra con !buscar en los sitios compatibles.")
        # (En Discord, !descargar ya busca la obra por su título antes de llegar aquí.)
        return
    log_callback("[ERROR] Unsupported website.")


def host_de_url(url: str) -> Optional[str]:
    """Hostname en minúsculas de una URL http(s), sin usuario, contraseña ni puerto.
    None si la URL no es http/https, no tiene host o el host es una IP literal."""
    import ipaddress
    try:
        p = urlparse(url.strip())
        host = (p.hostname or "").rstrip(".").lower()
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or not host:
        return None
    try:
        ipaddress.ip_address(host)
        return None  # nunca una IP: ningún sitio soportado se sirve así
    except ValueError:
        return host


def dominio_coincide(host: str, dominio: str) -> bool:
    """True si `host` es `dominio` o un subdominio suyo ("www.hitomi.la" con "hitomi.la").
    Un dominio sin punto ("nhentai", "hentai2read") es un nombre de sitio con varios TLD: debe ser
    la etiqueta registrable del host ("nhentai.to", "www.nhentai.xxx"), no cualquier parte."""
    dominio = dominio.lower().strip(".")
    if "." in dominio:
        return host == dominio or host.endswith("." + dominio)
    etiquetas = host.split(".")
    return len(etiquetas) >= 2 and etiquetas[-2] == dominio


# Sitios pedidos que responden con el desafío de Cloudflare ("Just a moment", 403) incluso en
# robots.txt, el feed y /wp-json. No se intenta saltar la protección: no se les hace ninguna
# petición. El bot saca el título del propio enlace y busca la obra en los sitios compatibles.
BLOQUEADOS_CLOUDFLARE = ("chochox.com", "vercomicsporno.com")

# Dónde pone cada sitio al artista en el slug (visto en sus URLs archivadas):
#   chochox.com/alicia-goes-wonderland-2-<artista>/     -> al final
#   vercomicsporno.com/<artista>-sweet-trade-cloud-meadow -> al principio (si no es "traduccion exclusiva")
_ARTISTA_EN_SLUG = {"chochox.com": -1, "vercomicsporno.com": 0}
_RUIDO_SLUG = {"comic", "comics", "porno", "porn", "xxx", "hentai", "traduccion", "exclusiva", "espanol",
               "spanish", "sexo", "historieta", "manga"}
_NO_SON_OBRAS = {"category", "tag", "artistas", "artista", "author", "page", "comics-porno", "search",
                 "categoria", "etiqueta"}


def obra_desde_enlace(url: str) -> Optional[tuple]:
    """Para un enlace de un sitio de BLOQUEADOS_CLOUDFLARE: (texto para buscar, artista probable o
    None, texto completo del slug). None si no es de esos sitios o el enlace no es de una obra
    (categoría, artista, portada...). Se lee solo la URL: nunca se pide nada a esos sitios."""
    import re
    from urllib.parse import unquote
    try:
        p = urlparse(url)
    except ValueError:
        return None
    host = (p.hostname or "").lower()
    sitio = next((d for d in BLOQUEADOS_CLOUDFLARE if dominio_coincide(host, d)), None)
    if not sitio:
        return None
    segmentos = [s for s in unquote(p.path).split("/") if s]
    if len(segmentos) != 1 or segmentos[0].lower() in _NO_SON_OBRAS:
        return None
    palabras = [re.sub(r"[^\w]", "", w) for w in re.split(r"[-_\s]+", segmentos[0])]
    palabras = [w for w in palabras if w and w.lower() not in _RUIDO_SLUG]
    if not any(re.search(r"[^\W\d_]", w) for w in palabras):  # "9572-2": solo números
        return None
    completo = " ".join(palabras)
    artista = None
    tenia_ruido = len(palabras) != len([w for w in re.split(r"[-_\s]+", segmentos[0]) if w])
    # Artista solo si quedan palabras suficientes para el título; en vercomicsporno los slugs
    # "...-traduccion-exclusiva" no llevan artista delante.
    if len(palabras) >= 3 and not (sitio == "vercomicsporno.com" and tenia_ruido):
        i = _ARTISTA_EN_SLUG[sitio]
        if not palabras[i].isdigit():  # "alicia-goes-wonderland-2": el 2 es la parte, no el artista
            artista = palabras[i]
            palabras = palabras[:-1] if i == -1 else palabras[1:]
    return " ".join(palabras), artista, completo
