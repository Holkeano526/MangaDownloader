"""
Capa HTTP compartida del proyecto.

Todo pasa por aiohttp (rápido y asíncrono). Cuando un sitio contesta con un desafío
de Cloudflare —403/429/503 con "Just a moment...", ``cf-mitigated: challenge``,
``challenge-platform``, ``cf_chl_*``...— la misma petición se repite con
`cloudscraper <https://github.com/venomous/cloudscraper>`_ en un hilo aparte, así que
el event loop sigue libre.

Uso (idéntico a aiohttp.ClientSession en lo que usa el proyecto)::

    from core.http import crear_sesion

    async with crear_sesion(headers=cabeceras, timeout=timeout) as s:
        async with s.get(url) as r:
            texto = await r.text(errors="replace")

Detalles:

* cloudscraper es síncrono (requests). Se ejecuta con ``asyncio.to_thread`` y cada
  hilo mantiene su propio scraper; las cookies (``cf_clearance``) se comparten entre
  hilos para no resolver el mismo desafío una y otra vez.
* Tras resolver un desafío, el host se marca durante ``_TTL_HOST_CF`` segundos y las
  siguientes peticiones van directas a cloudscraper (evita un 403 inútil por página).
* Si cloudscraper no está instalado, todo sigue funcionando como aiohttp normal.
* No resuelve captchas interactivas ni Turnstile: para esos casos el sitio devuelve
  igualmente su página de desafío y el handler muestra el error.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from typing import Any, Mapping, Optional
from urllib.parse import urlparse

import aiohttp

from . import config

try:  # cloudscraper es opcional: sin él, la capa funciona como aiohttp normal
    import cloudscraper
    import requests
    from cloudscraper.exceptions import CloudflareException
    _HAY_CLOUDSCRAPER = True
except ImportError:  # pragma: no cover - depende de cómo se instale el proyecto
    cloudscraper = None  # type: ignore[assignment]
    requests = None  # type: ignore[assignment]
    CloudflareException = Exception  # type: ignore[assignment,misc]
    _HAY_CLOUDSCRAPER = False


logger = logging.getLogger(__name__)

# Marcas de un desafío de Cloudflare en el cuerpo de la respuesta.
_RETO = re.compile(
    rb"just a moment|cf-chl|challenge-platform|cf_chl|__cf_chl|cf-mitigated|turnstile",
    re.I,
)

# Estados en los que Cloudflare sirve el desafío.
_ESTADOS_RETO = (403, 429, 503)

# Hosts que ya han necesitado cloudscraper: se recuerdan unos minutos.
_TTL_HOST_CF = 600.0
_hosts_cf: dict[str, float] = {}
_hosts_lock = threading.Lock()

# Scraper por hilo + cookies compartidas (cf_clearance sirve para todas las peticiones).
_local = threading.local()
_cookies = requests.cookies.RequestsCookieJar() if _HAY_CLOUDSCRAPER else None
_cookies_lock = threading.Lock()

# Argumentos de petición que cloudscraper (requests) entiende.
_KW_SCRAPER = ("headers", "params", "data", "json", "allow_redirects", "auth", "cookies")


class ErrorHTTP(aiohttp.ClientError):
    """Fallo de la capa HTTP, incluidos los errores de cloudscraper.

    Hereda de ``aiohttp.ClientError`` para que los ``except`` que ya existen en los
    handlers sigan capturándolo.
    """


class Cabeceras(dict):
    """Diccionario de cabeceras insensible a mayúsculas, como el de aiohttp."""

    def __init__(self, datos: Optional[Mapping[str, Any]] = None):
        super().__init__()
        for clave, valor in dict(datos or {}).items():
            self[str(clave).lower()] = valor

    def get(self, clave: str, defecto: Any = None) -> Any:
        return super().get(str(clave).lower(), defecto)

    def __getitem__(self, clave: str) -> Any:
        return super().__getitem__(str(clave).lower())

    def __contains__(self, clave: object) -> bool:
        return super().__contains__(str(clave).lower())


def _tipo_de_contenido(cabeceras: Mapping[str, Any]) -> str:
    return str(cabeceras.get("Content-Type") or "").split(";")[0].strip().lower()


def _codificacion(cabeceras: Mapping[str, Any]) -> Optional[str]:
    m = re.search(r"charset\s*=\s*[\"']?([\w.-]+)", str(cabeceras.get("Content-Type") or ""), re.I)
    return m.group(1) if m else None


def _segundos(timeout: Any) -> float:
    """Convierte un aiohttp.ClientTimeout (o un número) en segundos para requests."""
    if timeout is None:
        return 30.0
    total = getattr(timeout, "total", timeout)
    try:
        return float(total) if total else 30.0
    except (TypeError, ValueError):
        return 30.0


def es_reto_cloudflare(status: int, cabeceras: Mapping[str, Any], cuerpo: Any = b"") -> bool:
    """True si la respuesta es una página de desafío de Cloudflare."""
    if status not in _ESTADOS_RETO:
        return False
    if str(cabeceras.get("cf-mitigated") or "").lower() == "challenge":
        return True
    if isinstance(cuerpo, str):
        cuerpo = cuerpo.encode("utf-8", "replace")
    return bool(cuerpo and _RETO.search(cuerpo[:8192]))


class RespuestaHTTP:
    """Respuesta mínima con la API que usan los handlers de aiohttp.

    El cuerpo se lee entero (las páginas y las imágenes del proyecto siempre se leen
    así), de modo que la respuesta se puede devolver después de cerrar la conexión.
    """

    __slots__ = ("status", "url", "headers", "_cuerpo", "content_type", "reason")

    def __init__(self, status: int, url: Any, headers: Optional[Mapping[str, Any]] = None,
                 cuerpo: bytes = b"", content_type: Optional[str] = None, reason: str = ""):
        self.status = int(status)
        self.url = str(url)
        self.headers = headers if isinstance(headers, Cabeceras) else Cabeceras(headers)
        self._cuerpo = cuerpo or b""
        self.content_type = content_type or _tipo_de_contenido(self.headers)
        self.reason = reason

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    async def read(self) -> bytes:
        return self._cuerpo

    async def text(self, encoding: Optional[str] = None, errors: str = "strict") -> str:
        codificacion = encoding or _codificacion(self.headers) or "utf-8"
        try:
            return self._cuerpo.decode(codificacion, errors=errors)
        except (LookupError, UnicodeDecodeError):
            return self._cuerpo.decode("utf-8", errors="replace")

    async def json(self, encoding: Optional[str] = None, **kw: Any) -> Any:
        return json.loads(await self.text(encoding=encoding), **kw)

    def release(self) -> None:
        """Compatibilidad con aiohttp (la conexión ya está cerrada)."""

    def __repr__(self) -> str:  # pragma: no cover - ayuda al depurar
        return f"<RespuestaHTTP {self.status} {self.url}>"


def _host(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except ValueError:
        return ""


def _marcar_host(url: str) -> None:
    with _hosts_lock:
        _hosts_cf[_host(url)] = time.monotonic() + _TTL_HOST_CF


def _desmarcar_host(url: str) -> None:
    with _hosts_lock:
        _hosts_cf.pop(_host(url), None)


def _host_marcado(url: str) -> bool:
    host = _host(url)
    with _hosts_lock:
        hasta = _hosts_cf.get(host)
        if hasta is None:
            return False
        if hasta > time.monotonic():
            return True
        _hosts_cf.pop(host, None)
        return False


def _scraper() -> Any:
    """Scraper de cloudscraper para el hilo actual (con las cookies compartidas)."""
    if not _HAY_CLOUDSCRAPER:
        raise ErrorHTTP("cloudscraper no está instalado")
    scraper = getattr(_local, "scraper", None)
    if scraper is None:
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False},
            delay=5,
        )
        scraper.headers.update({
            "User-Agent": config.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                      "image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        })
        _local.scraper = scraper
    with _cookies_lock:
        scraper.cookies.update(_cookies)
    return scraper


def _pedir_cloudscraper(metodo: str, url: str, opciones: Mapping[str, Any], timeout: Any) -> RespuestaHTTP:
    """Petición síncrona con cloudscraper. Se llama desde ``asyncio.to_thread``."""
    scraper = _scraper()
    try:
        r = scraper.request(metodo, url, timeout=_segundos(timeout), **dict(opciones))
    except CloudflareException as e:
        raise ErrorHTTP(f"Cloudflare no se pudo resolver en {_host(url)}: {e}") from e
    except requests.RequestException as e:  # type: ignore[union-attr]
        raise ErrorHTTP(f"Falló la petición a {_host(url)}: {e}") from e

    with _cookies_lock:
        # El jar entero: cloudscraper guarda ahí el cf_clearance que resuelve el desafío.
        _cookies.update(scraper.cookies)

    return RespuestaHTTP(
        r.status_code,
        r.url,
        r.headers,
        r.content,
        content_type=_tipo_de_contenido(r.headers),
        reason=str(r.reason or ""),
    )


class _Peticion:
    """Context manager de una petición con reintento transparente por Cloudflare."""

    __slots__ = ("_sesion", "_metodo", "_url", "_kw")

    def __init__(self, sesion: "SesionHTTP", metodo: str, url: str, **kw: Any):
        self._sesion = sesion
        self._metodo = metodo
        self._url = url
        self._kw = kw

    async def __aenter__(self) -> RespuestaHTTP:
        return await self._ejecutar()

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    def _opciones_scraper(self) -> dict:
        opciones = {k: v for k, v in self._kw.items() if k in _KW_SCRAPER and v is not None}
        # aiohttp aplica las cabeceras de la sesión solo; cloudscraper necesita recibirlas.
        cabeceras = dict(self._sesion._headers)
        cabeceras.update(self._kw.get("headers") or {})
        if cabeceras:
            opciones["headers"] = cabeceras
        opciones.setdefault("allow_redirects", True)
        return opciones

    def _timeout(self) -> Any:
        return self._kw.get("timeout") or self._sesion._timeout

    async def _por_cloudscraper(self) -> RespuestaHTTP:
        return await asyncio.to_thread(
            _pedir_cloudscraper, self._metodo, self._url, self._opciones_scraper(), self._timeout()
        )

    async def _ejecutar(self) -> RespuestaHTTP:
        # Host ya conocido como protegido: no se gasta el primer 403 en cada página.
        if _host_marcado(self._url):
            try:
                return await self._por_cloudscraper()
            except ErrorHTTP as e:
                logger.warning("%s", e)
                _desmarcar_host(self._url)  # el sitio cambió: se vuelve a probar aiohttp

        async with self._sesion._aio.request(self._metodo, self._url, **self._kw) as r:
            cuerpo = b"" if self._metodo == "HEAD" else await r.read()
            respuesta = RespuestaHTTP(
                r.status,
                r.url,
                r.headers,
                cuerpo,
                content_type=r.content_type,
                reason=str(r.reason or ""),
            )

        if not (_HAY_CLOUDSCRAPER and es_reto_cloudflare(respuesta.status, respuesta.headers, cuerpo)):
            return respuesta

        logger.info("Cloudflare en %s (%s): reintentando con cloudscraper",
                    _host(self._url), respuesta.status)
        try:
            nueva = await self._por_cloudscraper()
        except ErrorHTTP as e:
            logger.warning("%s", e)
            return respuesta  # el handler ya sabe tratar el 403/503

        if es_reto_cloudflare(nueva.status, nueva.headers, nueva._cuerpo):
            logger.warning("cloudscraper no resolvió el desafío de %s", _host(self._url))
            return nueva

        _marcar_host(self._url)
        return nueva


class SesionHTTP:
    """Sustituto ligero de ``aiohttp.ClientSession`` con plan B para Cloudflare."""

    def __init__(self, **kwargs: Any):
        # Comodidad: aceptar timeout=30 además de aiohttp.ClientTimeout(total=30).
        if isinstance(kwargs.get("timeout"), (int, float)):
            kwargs["timeout"] = aiohttp.ClientTimeout(total=float(kwargs["timeout"]))
        self._headers = dict(kwargs.get("headers") or {})
        self._timeout = kwargs.get("timeout")
        self._aio = aiohttp.ClientSession(**kwargs)

    def get(self, url: str, **kw: Any) -> _Peticion:
        return _Peticion(self, "GET", url, **kw)

    def post(self, url: str, **kw: Any) -> _Peticion:
        return _Peticion(self, "POST", url, **kw)

    def put(self, url: str, **kw: Any) -> _Peticion:
        return _Peticion(self, "PUT", url, **kw)

    def patch(self, url: str, **kw: Any) -> _Peticion:
        return _Peticion(self, "PATCH", url, **kw)

    def delete(self, url: str, **kw: Any) -> _Peticion:
        return _Peticion(self, "DELETE", url, **kw)

    def head(self, url: str, **kw: Any) -> _Peticion:
        return _Peticion(self, "HEAD", url, **kw)

    async def close(self) -> None:
        await self._aio.close()

    async def __aenter__(self) -> "SesionHTTP":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()


def crear_sesion(**kwargs: Any) -> SesionHTTP:
    """Crea una sesión con reintento transparente ante Cloudflare.

    Acepta los mismos argumentos que ``aiohttp.ClientSession`` (headers, timeout,
    connector...).
    """
    return SesionHTTP(**kwargs)
