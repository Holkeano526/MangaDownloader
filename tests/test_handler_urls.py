"""Filtro de URLs del router (prevención de SSRF): host_de_url y dominio_coincide."""
import pytest

from core.handler import HANDLERS, dominio_coincide, host_de_url


def _handler_para(url):
    """Igual que process_entry: el primer handler cuyo dominio coincide con el host real."""
    host = host_de_url(url)
    if not host:
        return None
    for h in HANDLERS:
        if any(dominio_coincide(host, d) for d in h.get_supported_domains()):
            return h
    return None


@pytest.mark.parametrize("url", [
    "https://sexkomix2.com@192.168.1.1/",
    "http://127.0.0.1:8080/g/1",
    "http://[::1]/",
    "http://10.0.0.5/hitomi.la",
    "file:///etc/passwd",
    "ftp://hitomi.la/x",
    "javascript:alert(1)",
    "hitomi.la/galleries/1.html",  # sin esquema
    "",
])
def test_host_de_url_rechaza(url):
    assert host_de_url(url) is None


def test_host_de_url_quita_usuario_puerto_y_mayusculas():
    assert host_de_url("https://user:pw@WWW.Hitomi.LA:443/reader/1.html") == "www.hitomi.la"
    assert host_de_url("https://hitomi.la./x") == "hitomi.la"


@pytest.mark.parametrize("host,dominio", [
    ("nhentai.10.0.0.5.nip.io", "nhentai"),
    ("hitomi.la.evil.com", "hitomi.la"),
    ("evilhitomi.la", "hitomi.la"),
    ("nhentai", "nhentai"),
    ("hentai2read.com.evil.net", "hentai2read"),
])
def test_dominio_coincide_rechaza(host, dominio):
    assert not dominio_coincide(host, dominio)


@pytest.mark.parametrize("host,dominio", [
    ("nhentai.to", "nhentai"),
    ("www.nhentai.xxx", "nhentai"),
    ("hentai2read.com", "hentai2read"),
    ("hitomi.la", "hitomi.la"),
    ("www.hitomi.la", "hitomi.la"),
    ("hitomi.la", "HITOMI.LA."),
])
def test_dominio_coincide_acepta(host, dominio):
    assert dominio_coincide(host, dominio)


@pytest.mark.parametrize("url,clase", [
    ("https://nhentai.to/g/12345/", "NHentaiMirrorHandler"),
    ("https://www.nhentai.xxx/g/12345/", "NHentaiMirrorHandler"),
    ("https://hentai2read.com/hentai/123-x/", "H2RHandler"),
    ("https://hitomi.la/reader/123.html", "HitomiHandler"),
])
def test_enrutado_valido(url, clase):
    assert type(_handler_para(url)).__name__ == clase


@pytest.mark.parametrize("url", [
    "https://sexkomix2.com@192.168.1.1/",
    "http://nhentai.10.0.0.5.nip.io/g/1",
    "https://hitomi.la.evil.com/reader/1.html",
    "file:///C:/Windows/win.ini",
])
def test_enrutado_ssrf_sin_handler(url):
    assert _handler_para(url) is None
