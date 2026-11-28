"""Funciones puras de core.utils: nombres de archivo, reserva de salidas, firmas de imagen y temporales."""
import asyncio
import os
import time

import pytest

from core import utils
from core.config import TEMP_FOLDER_NAME


# ─── clean_filename ──────────────────────────────────────────────────────────

def test_clean_filename_vacio():
    assert utils.clean_filename("") == "untitled"
    assert utils.clean_filename(None) == "untitled"
    assert utils.clean_filename("???***") == "untitled"


def test_clean_filename_caracteres_reservados_y_html():
    assert utils.clean_filename('<b>Mi</b> Título: "uno"/dos*?|') == "Mi Título unodos"
    assert utils.clean_filename("a\\b") == "ab"


def test_clean_filename_quita_caracteres_de_control():
    assert utils.clean_filename("hola\x00\x07\nmundo\x1f\x7f") == "holamundo"


def test_clean_filename_quita_punto_y_espacio_final():
    assert utils.clean_filename("Capítulo 1...") == "Capítulo 1"
    assert utils.clean_filename("  título .  ") == "título"


def test_clean_filename_trunca_a_150_bytes_sin_romper_utf8():
    japones = "あ" * 90  # 3 bytes por carácter = 270 bytes
    r = utils.clean_filename(japones)
    assert utils.MAX_BYTES_NOMBRE == 150
    assert len(r.encode("utf-8")) <= 150
    assert r == "あ" * 50
    assert utils.clean_filename("x" * 400) == "x" * 150


def test_clean_filename_truncado_no_deja_punto_final():
    assert utils.clean_filename("a" * 149 + ".bbbb") == "a" * 149


# ─── _reservar_salida / _liberar_salida ──────────────────────────────────────

def test_reservar_salida_numera_las_ocupadas(tmp_path):
    ruta = str(tmp_path / "x.pdf")
    a = utils._reservar_salida(ruta)
    b = utils._reservar_salida(ruta)
    c = utils._reservar_salida(ruta)
    try:
        assert a == ruta
        assert b == str(tmp_path / "x (2).pdf")
        assert c == str(tmp_path / "x (3).pdf")
        utils._liberar_salida(a)
        assert utils._reservar_salida(ruta) == ruta  # liberada, se vuelve a dar
    finally:
        for r in (a, b, c):
            utils._liberar_salida(r)
    assert not any(os.path.normcase(str(tmp_path)) in s for s in utils._SALIDAS_EN_USO)


def test_liberar_salida_desconocida_no_falla(tmp_path):
    utils._liberar_salida(str(tmp_path / "nunca.pdf"))


def test_nombre_pdf_seguro():
    assert utils._nombre_pdf_seguro("a/b:c.PDF") == "abc.pdf"
    assert utils._nombre_pdf_seguro("x" * 300) == "x" * 150 + ".pdf"


# ─── _es_imagen ──────────────────────────────────────────────────────────────

def _es_imagen(tipo, datos):
    return asyncio.run(utils._es_imagen(tipo, datos))


@pytest.mark.parametrize("datos", [
    b"\xff\xd8\xff\xe0" + b"\x00" * 20,                 # JPEG
    b"\x89PNG\r\n\x1a\n" + b"\x00" * 20,                # PNG
    b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 20,     # WebP
    b"GIF89a" + b"\x00" * 20,                           # GIF
    b"\x00\x00\x00\x1cftypavif" + b"\x00" * 20,         # AVIF
])
def test_es_imagen_firmas(datos):
    assert _es_imagen("image/whatever", datos)
    assert _es_imagen("application/octet-stream", datos)


def test_es_imagen_rechaza_html_y_vacio():
    html = b"<!DOCTYPE html><html><head><title>Just a moment...</title></head></html>"
    assert not _es_imagen("text/html; charset=utf-8", html)
    assert not _es_imagen("image/jpeg", html)  # el Content-Type miente: decide el contenido
    assert not _es_imagen("image/jpeg", b"")
    assert not _es_imagen("application/json", b"\xff\xd8\xff")


# ─── carpetas temporales ─────────────────────────────────────────────────────

def test_nueva_carpeta_temporal_bajo_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    a = utils.nueva_carpeta_temporal("hitomi_123")
    b = utils.nueva_carpeta_temporal("hitomi_123")
    assert a != b
    for ruta in (a, b):
        assert os.path.isdir(ruta)
        assert os.path.dirname(ruta) == os.path.join(str(tmp_path), TEMP_FOLDER_NAME)
        assert os.path.basename(ruta).startswith("hitomi_123_")


def test_nueva_carpeta_temporal_sanea_prefijo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ruta = utils.nueva_carpeta_temporal("../../fuera:*")
    assert os.path.dirname(ruta) == os.path.join(str(tmp_path), TEMP_FOLDER_NAME)


def test_limpiar_temporales_huerfanos(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert utils.limpiar_temporales_huerfanos() == 0  # aún no existe la carpeta temporal

    vieja = utils.nueva_carpeta_temporal("vieja")
    nueva = utils.nueva_carpeta_temporal("nueva")
    archivo_viejo = os.path.join(str(tmp_path), TEMP_FOLDER_NAME, "suelto.txt")
    with open(os.path.join(vieja, "001.jpg"), "wb") as f:
        f.write(b"x")
    with open(archivo_viejo, "w") as f:
        f.write("x")
    hace_7h = time.time() - 7 * 3600
    os.utime(vieja, (hace_7h, hace_7h))
    os.utime(archivo_viejo, (hace_7h, hace_7h))

    assert utils.limpiar_temporales_huerfanos(max_horas=6) == 1
    assert not os.path.exists(vieja)
    assert os.path.isdir(nueva)
    assert os.path.exists(archivo_viejo)  # solo borra carpetas
