"""Pruebas de la capa HTTP compartida (core/http.py).

Se ejecutan sin red: la respuesta de aiohttp y la de cloudscraper se simulan.
    python -m unittest discover -s tests -v
"""
import unittest
from unittest.mock import patch

import core.http as http
from core.http import RespuestaHTTP, SesionHTTP, es_reto_cloudflare


class _RespuestaAioFalsa:
    """Imita lo justo de una respuesta de aiohttp que usa _Peticion."""

    def __init__(self, status, cuerpo=b"", headers=None, url="https://ejemplo.com/pagina"):
        self.status = status
        self._cuerpo = cuerpo
        self.headers = headers or {}
        self.url = url
        self.content_type = "text/html"
        self.reason = "Forbidden"

    async def read(self):
        return self._cuerpo

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _AioFalsa:
    """Imita aiohttp.ClientSession.request."""

    def __init__(self, respuesta):
        self.respuesta = respuesta
        self.llamadas = 0

    def request(self, metodo, url, **kw):
        self.llamadas += 1
        return self.respuesta

    async def close(self):
        pass


def _sesion(respuesta):
    """SesionHTTP sin abrir una ClientSession real."""
    s = SesionHTTP.__new__(SesionHTTP)
    s._headers = {}
    s._timeout = None
    s._aio = _AioFalsa(respuesta)
    return s


class DeteccionTest(unittest.TestCase):
    def test_detecta_desafios(self):
        self.assertTrue(es_reto_cloudflare(403, {}, b"<title>Just a moment...</title>"))
        self.assertTrue(es_reto_cloudflare(503, {}, b"<script>challenge-platform</script>"))
        self.assertTrue(es_reto_cloudflare(403, {"cf-mitigated": "challenge"}, b""))
        self.assertTrue(es_reto_cloudflare(403, {}, "<title>Just a moment...</title>"))

    def test_no_confunde_errores_normales(self):
        self.assertFalse(es_reto_cloudflare(403, {}, b"<h1>Forbidden</h1>"))
        self.assertFalse(es_reto_cloudflare(200, {}, b"Just a moment"))
        self.assertFalse(es_reto_cloudflare(404, {}, b"cf-chl"))


class ReintentoTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        http._hosts_cf.clear()

    async def test_reintenta_con_cloudscraper(self):
        aio = _RespuestaAioFalsa(403, b"<title>Just a moment...</title>")
        sesion = _sesion(aio)
        nueva = RespuestaHTTP(200, "https://ejemplo.com/pagina", {}, b"contenido real")

        with patch("core.http._pedir_cloudscraper", return_value=nueva) as pedir:
            async with sesion as s:
                async with s.get("https://ejemplo.com/pagina") as r:
                    self.assertEqual(r.status, 200)
                    self.assertEqual(await r.text(), "contenido real")
            pedir.assert_called_once()

        # El host queda marcado: la siguiente petición va directa a cloudscraper.
        with patch("core.http._pedir_cloudscraper", return_value=nueva) as pedir:
            async with sesion as s:
                async with s.get("https://ejemplo.com/otra") as r:
                    self.assertEqual(r.status, 200)
            pedir.assert_called_once()
        self.assertEqual(sesion._aio.llamadas, 1)

    async def test_sin_cloudscraper_devuelve_el_403(self):
        aio = _RespuestaAioFalsa(403, b"<title>Just a moment...</title>")
        sesion = _sesion(aio)

        with patch.object(http, "_HAY_CLOUDSCRAPER", False):
            with patch("core.http._pedir_cloudscraper") as pedir:
                async with sesion as s:
                    async with s.get("https://ejemplo.com/pagina") as r:
                        self.assertEqual(r.status, 403)
                pedir.assert_not_called()

    async def test_si_cloudscraper_falla_devuelve_la_respuesta_original(self):
        aio = _RespuestaAioFalsa(503, b"<title>Just a moment...</title>")
        sesion = _sesion(aio)

        with patch("core.http._pedir_cloudscraper", side_effect=http.ErrorHTTP("sin resolver")):
            async with sesion as s:
                async with s.get("https://ejemplo.com/pagina") as r:
                    self.assertEqual(r.status, 503)


class SesionTest(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_entero_se_convierte(self):
        async with http.crear_sesion(timeout=5) as s:
            self.assertEqual(s._timeout.total, 5)


class RespuestaTest(unittest.IsolatedAsyncioTestCase):
    async def test_texto_con_charset(self):
        r = RespuestaHTTP(200, "https://ejemplo.com/", {"Content-Type": "text/html; charset=iso-8859-1"},
                          "ñandú".encode("iso-8859-1"))
        self.assertEqual(await r.text(), "ñandú")

    async def test_json(self):
        r = RespuestaHTTP(200, "https://ejemplo.com/", {"Content-Type": "application/json"},
                          b'{"a": 1}')
        self.assertEqual(await r.json(), {"a": 1})

    def test_cabeceras_insensibles_a_mayusculas(self):
        r = RespuestaHTTP(200, "https://ejemplo.com/", {"Content-Length": "12"})
        self.assertEqual(r.headers.get("content-length"), "12")
        self.assertIn("CONTENT-LENGTH", r.headers)


if __name__ == "__main__":
    unittest.main()
