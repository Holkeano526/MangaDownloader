"""Funciones puras del handler de X/Twitter (sin red)."""
from core.sites import twitter as tw
from core.handler import host_de_url, dominio_coincide


def test_enlace_canonico_quita_seguimiento_y_espejos():
    assert tw.enlace_canonico("https://twitter.com/a/status/123?s=20&t=xyz") == "https://x.com/a/status/123"
    assert tw.enlace_canonico("https://vxtwitter.com/a/status/123/video/1") == "https://x.com/a/status/123/video/1"


def test_id_tuit():
    assert tw.id_tuit("https://x.com/usuario/status/1790000000000000000") == "1790000000000000000"
    assert tw.id_tuit("https://twitter.com/i/web/status/910031516746514432") == "910031516746514432"
    assert tw.id_tuit("https://x.com/usuario") is None


def test_mejor_mp4_elige_la_directa_de_mas_calidad():
    formatos = [
        {"format_id": "http-320", "protocol": "https", "ext": "mp4", "vcodec": None, "height": 180, "tbr": 320, "url": "a"},
        {"format_id": "http-2176", "protocol": "https", "ext": "mp4", "vcodec": None, "height": 720, "tbr": 2176, "url": "c"},
        {"format_id": "hls-2176", "protocol": "m3u8_native", "ext": "mp4", "vcodec": "avc1", "height": 720, "tbr": 2176, "url": "h"},
        {"format_id": "http-832", "protocol": "https", "ext": "mp4", "vcodec": None, "height": 360, "tbr": 832, "url": "b"},
    ]
    assert tw.mejor_mp4(formatos)["format_id"] == "http-2176"
    assert tw.mejor_mp4([formatos[2]]) is None   # solo HLS: no se puede sin ffmpeg
    assert tw.mejor_mp4([]) is None


def test_motivo_error_traduce_los_casos_conocidos():
    assert "iniciar sesión" in tw.motivo_error("ERROR: [twitter] 1: NSFW tweet requires authentication")
    assert "no tiene vídeo" in tw.motivo_error("ERROR: [twitter] 1: No video could be found in this tweet")
    assert "no existe" in tw.motivo_error("ERROR: Requested tweet is unavailable")


def test_dominios_de_twitter_pasan_el_filtro_ssrf():
    for url in ("https://x.com/a/status/1", "https://mobile.twitter.com/a/status/1",
                "https://fxtwitter.com/a/status/1"):
        host = host_de_url(url)
        assert any(dominio_coincide(host, d) for d in tw.DOMINIOS)
    assert not any(dominio_coincide(host_de_url("https://x.com.evil.io/a/status/1"), d) for d in tw.DOMINIOS)
