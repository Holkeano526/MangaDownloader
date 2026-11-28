# Añadir un sitio a MangaDownloader

Cada sitio es una clase aislada en `core/sites/` (patrón Strategy). El bot de Discord la usa sin cambios.

## 1. Descarga

1. Crea `core/sites/nuevositio.py` con una clase que herede de `BaseSiteHandler`.
2. Expórtala en `core/sites/__init__.py` y añádela a `HANDLERS` en `core/handler.py`.
   **El orden importa:** gana el primer handler cuyo dominio aparezca en el hostname de la URL, así que los
   dominios más específicos van antes.

Plantilla (HTTP puro, la opción preferida):

```python
import re
from typing import Callable, Optional

import aiohttp

from .base import BaseSiteHandler
from .. import config
from ..utils import clean_filename, download_and_make_pdf


class NuevoSitioHandler(BaseSiteHandler):
    """nuevositio.com — ficha /g/<id>, páginas en <img data-src>."""

    @staticmethod
    def get_supported_domains() -> list:
        return ["nuevositio.com"]

    async def process(self, url: str, log_callback: Callable[[str], None],
                      check_cancel: Callable[[], bool],
                      progress_callback: Optional[Callable[[int, int], None]] = None):
        cab = {"User-Agent": config.USER_AGENT, "Referer": "https://nuevositio.com/"}
        async with aiohttp.ClientSession(headers=cab) as s:
            async with s.get(url) as r:
                html = await r.text()

        titulo = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S).group(1).strip()
        paginas = re.findall(r'data-src="(https://[^"]+\.(?:jpg|png|webp))"', html)
        if not paginas:
            log_callback("[ERROR] No se encontraron páginas.")
            return None

        log_callback(f"[INFO] {titulo}: {len(paginas)} páginas")
        # Descarga en paralelo con reintentos, convierte a JPEG, arma el PDF y limpia temporales
        await download_and_make_pdf(paginas, f"{clean_filename(titulo)}.pdf", cab,
                                    log_callback, check_cancel, progress_callback)
```

Reglas:

- **Primero HTTP.** Busca la lista de páginas en el HTML, en un JSON incrustado o en la API que usa el lector.
  Usa Playwright solo si no hay otra forma, o como respaldo cuando el formato cambie.
- **No esquives protecciones** (Cloudflare, captchas, anti-bot). Si el sitio las tiene, registra un aviso
  claro y devuelve `None`.
- **No descargues tú las imágenes:** `download_and_make_pdf` ya hace el pool paralelo, los reintentos ante
  429/5xx, la conversión WebP/AVIF → JPEG y la cancelación sin dejar PDFs a medias.
- **Series:** si la URL es la página de una serie, devuelve `list[str]` con las URLs de los capítulos; las
  interfaces los ponen en cola.
- **Vídeos u otros archivos:** usa `descargar_archivo(url, destino, cabeceras, log_callback, check_cancel)` y
  guarda en `carpeta_media()` con `nombre_media(titulo, "mp4")`. Avisa con `[SUCCESS] File Generated: <ruta>`
  (el bot lo entrega igual que `[SUCCESS] PDF Generated:`). En los sitios de `!leak` **solo se descargan
  vídeos**; las fotos se ignoran.
- Comprueba el número de páginas real (algunos sitios responden 200 con 0 bytes para páginas que no existen).

## 2. Búsqueda (opcional)

En `core/busqueda.py`:

1. Escribe `async def _nuevositio(s, q)` que devuelva tuplas `(sitio, título, url, portada)`.
   `_enlaces(html, base, patron_url)` extrae las tarjetas de una página de resultados típica.
   Pide las páginas con `_get(s, url)`: respeta el tope global de peticiones simultáneas (`_SEM_HTTP`).
   Opcional, un 5.º elemento con el idioma del resultado tal como lo da el sitio, en inglés y en minúsculas
   (`"spanish"`, `"english"`, `"japanese"`...: las claves de `_IDIOMA_HITOMI`), p. ej.
   `(sitio, título, url, portada, "spanish")`. `buscar()` lo pasa a `idioma_de()` como pista y manda sobre
   lo que diga el título; si el valor no está en `_IDIOMA_HITOMI` se ignora. Lo usan Hitomi y SexKomix2.
2. Añádela a `BUSCADORES_MANGA` (no a `BUSCADORES`, que es la suma de las dos listas y se calcula sola).
3. Si el sitio tiene un solo idioma, añádelo a `_IDIOMA_SITIO`. Si sus resultados son fiables aunque el título
   no coincida (también busca por artista o etiqueta), añádelo a `ESTRICTOS`.
4. Si es un sitio de creadoras (catálogo grande, búsqueda por nombre/etiqueta), añádelo a
   `BUSCADORES_CREADORAS` (en vez de `BUSCADORES_MANGA`) y a `CREADORAS`: lo usa `!leak` (no `!buscar`), con
   una puntuación mínima y un tope por sitio para que no tape a los demás.

## 3. Probar

```bash
python -c "from core.handler import HANDLERS; print([type(h).__name__ for h in HANDLERS])"
python -c "import asyncio; from core import busqueda; print(asyncio.run(busqueda.buscar('texto', buscadores=busqueda.BUSCADORES_MANGA))[:3])"
```

Y una descarga real con `!descargar <url>`.
