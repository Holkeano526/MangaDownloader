
from typing import Callable, Optional
from .sites import (
    TMOHandler, 
    M440Handler, 
    H2RHandler, 
    HitomiHandler, 
    NHentaiHandler, 
    ZonaTMOHandler
)

# Instantiate handlers (Stateless or Singleton approach)
HANDLERS = [
    TMOHandler(),
    M440Handler(),
    H2RHandler(),
    HitomiHandler(),
    NHentaiHandler(),
    ZonaTMOHandler()
]

async def process_entry(
    url: str,
    log_callback: Callable[[str], None],
    check_cancel: Callable[[], bool],
    progress_callback: Optional[Callable[[int, int], None]] = None
) -> None:
    """Main Router: Redirects to specific site handler based on URL."""
    for handler in HANDLERS:
        # Check if any supported domain matches the URL
        if any(domain in url for domain in handler.get_supported_domains()):
            await handler.process(url, log_callback, check_cancel, progress_callback)
            return
            
    log_callback("[ERROR] Unsupported website.")
