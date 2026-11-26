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
)

# Instantiate handlers (stateless). Gana el primero cuyo dominio aparezca en la URL.
HANDLERS = [
    TMOHentaiAppHandler(),
    LectorHentaiHandler(),
    HentaiNexusHandler(),
    HitomiHandler(),
    NHentaiMirrorHandler(),  # nhentai.* (to, xxx, website, net, top...) y 3hentai.net
    H2RHandler(),
    M440Handler(),
    HentalkHandler(),        # hentalk.pw y los enlaces viejos de fakku.cc (mismo servidor)
    ShadeMangaHandler(),
]

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
        return
    log_callback("[ERROR] Unsupported website.")


# Sitios pedidos que responden con el desafío de Cloudflare ("Just a moment", 403) incluso en
# robots.txt, el feed y /wp-json. No se intenta saltar la protección: solo se explica el motivo.
BLOQUEADOS_CLOUDFLARE = ("chochox.com", "vercomicsporno.com")
