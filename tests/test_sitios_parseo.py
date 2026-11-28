"""Parseo sin red de algunos sitios: gg.js de Hitomi, cifrado de HentaiNexus y JSON de Hentai2Read."""
import base64
import json

import pytest

from core.sites import h2r, hentainexus, hitomi_http


# ─── Hitomi: gg.js ───────────────────────────────────────────────────────────

GG_JS = """'use strict';
gg = {
m: function(g) {
var o = 0;
switch (g) {
case 3243:
case 17:
o = 1; break;
case 99:
o = 0; break;
}
return o;
},
s: function(h) { var m = /(..)(.)$/.exec(h); return parseInt(m[2]+m[1], 16).toString(10); },
b: '1732000000/'
};
"""

HASH = "0" * 61 + "abc"  # g = int("c" + "ab", 16) = 3243


def test_reglas_gg_desde_js():
    r = hitomi_http.ReglasGG.desde_js(GG_JS)
    assert r.b == "1732000000/"
    assert r.por_defecto == 0
    assert r.casos == {3243: 1, 17: 1, 99: 0}
    assert r.m(3243) == 1 and r.m(5) == 0
    assert hitomi_http.ReglasGG.s(HASH) == "3243"


def test_url_imagen():
    r = hitomi_http.ReglasGG.desde_js(GG_JS)
    assert hitomi_http.url_imagen(HASH, r) == \
        f"https://w2.{hitomi_http.DOMINIO}/1732000000/3243/{HASH}.webp"
    otro = "0" * 61 + "001"  # g = 0x100 = 256 -> valor por defecto
    assert hitomi_http.url_imagen(otro, r, ext="avif").startswith(f"https://a1.{hitomi_http.DOMINIO}/")


@pytest.mark.parametrize("js", [
    GG_JS.replace("b: '1732000000/'", ""),
    GG_JS.replace("var o = 0;", ""),
    "var o = 0; b: '1/'",  # sin casos
])
def test_reglas_gg_formato_inesperado(js):
    with pytest.raises(hitomi_http.ErrorResolucion):
        hitomi_http.ReglasGG.desde_js(js)


def test_url_imagen_hash_invalido():
    r = hitomi_http.ReglasGG.desde_js(GG_JS)
    with pytest.raises(hitomi_http.ErrorResolucion):
        hitomi_http.url_imagen("../../etc/passwd", r)


# ─── HentaiNexus: descifrar ──────────────────────────────────────────────────

def _cifrar(texto: str, hostname: str = hentainexus.HOSTNAME) -> str:
    """Inverso de hentainexus.descifrar: el flujo tipo RC4 es simétrico (XOR), con una clave fija."""
    clave = bytes((i * 37 + 11) % 256 for i in range(64))
    c = 0
    for b in clave:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ 0xC if c & 1 else c >> 1
    paso = hentainexus._primos(16)[c & 7]
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + clave[i % 64]) % 256
        s[i], s[j] = s[j], s[i]
    claro = texto.encode("latin-1")
    cifrado = bytearray()
    a = x = cc = m = 0
    for n in range(len(claro)):
        a = (a + paso) % 256
        x = (cc + s[(x + s[a]) % 256]) % 256
        cc = (cc + a + s[a]) % 256
        s[a], s[x] = s[x], s[a]
        m = s[(x + s[(a + s[(m + cc) % 256]) % 256]) % 256]
        cifrado.append(claro[n] ^ m)
    blob = bytearray(clave) + cifrado
    for i in range(min(len(hostname), 64)):
        blob[i] ^= ord(hostname[i])
    return base64.b64encode(bytes(blob)).decode()


PAGINAS = [
    {"type": "image", "image_fallback": "https://images.hentainexus.com/v2/1/001.webp", "label": "1"},
    {"type": "image", "image_avif": "https://images.hentainexus.com/v2/1/002.avif", "label": "2"},
]


def test_descifrar_ida_y_vuelta():
    assert hentainexus.descifrar(_cifrar(json.dumps(PAGINAS))) == PAGINAS
    urls = [hentainexus._url_pagina(p) for p in PAGINAS]
    assert urls == [PAGINAS[0]["image_fallback"], PAGINAS[1]["image_avif"]]


def test_descifrar_con_otro_hostname_falla():
    with pytest.raises(hentainexus.ErrorDescifrado):
        hentainexus.descifrar(_cifrar(json.dumps(PAGINAS)), hostname="otro.example")


def test_descifrar_bloque_corto():
    with pytest.raises(hentainexus.ErrorDescifrado):
        hentainexus.descifrar(base64.b64encode(b"x" * 64).decode())


def test_primos():
    assert hentainexus._primos(8) == [2, 3, 5, 7, 11, 13, 17, 19]


# ─── Hentai2Read: JSON de Inertia ────────────────────────────────────────────

def test_h2r_datos_pagina():
    datos = {"component": "Reader", "props": {"images": ["https://x/1.jpg"], "chapter": {"title": "Cap 1"}}}
    html = ('<html><body><script data-page="app" type="application/json">'
            + json.dumps(datos) + "</script></body></html>")
    assert h2r.datos_pagina(html) == datos


def test_h2r_datos_pagina_sin_json():
    assert h2r.datos_pagina("<html></html>") == {}
    assert h2r.datos_pagina('<script data-page="app">{roto</script>') == {}
