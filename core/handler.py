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
    try:
        parsed_url = urlparse(url)
        hostname = parsed_url.netloc.lower()
    except Exception:
        log_callback("[ERROR] Invalid URL provided.")
        return

    for handler in HANDLERS:
        # [SEGURIDAD - OPEN SOURCE] 
        # Prevención de Server-Side Request Forgery (SSRF).
        # Validamos que el dominio base (ej. 'tmohentai') exista genuinamente dentro del 'hostname' extraído,
        # en lugar de hacer un simple 'domain in url' que permitiría a un atacante inyectar URLs internas
        # como: `http://localhost:8000/api/admin?fake=tmohentai`.
        # Esto asegura que la petición HTTP que hace el servidor web vaya dirigida exclusivamente 
        # a los dominios oficiales de manga soportados.
        supported = handler.get_supported_domains()
        if any(domain in hostname for domain in handler.get_supported_domains()):
            return await handler.process(url, log_callback, check_cancel, progress_callback)

    if any(d in hostname for d in BLOQUEADOS_CLOUDFLARE):
        log_callback(f"[ERROR] {hostname} está protegido por Cloudflare (desafío anti-bots en todas sus páginas) "
                     f"y no se puede descargar automáticamente. Busca la obra con !buscar en los sitios compatibles.")
        # (En Discord, !descargar ya busca la obra por su título antes de llegar aquí.)
        return
    log_callback("[ERROR] Unsupported website.")


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
    host = p.netloc.lower().split(":")[0]
    sitio = next((d for d in BLOQUEADOS_CLOUDFLARE if host == d or host.endswith("." + d)), None)
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
