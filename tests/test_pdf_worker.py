"""core.pdf_worker: PDF desde imágenes, escribiendo en <salida>.part y renombrando al final."""
import os
import re

from PIL import Image

from core import pdf_worker


def _imagen(ruta, color, modo="RGB"):
    Image.new(modo, (20, 30), color).save(ruta)
    return str(ruta)


def _paginas(pdf: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page(?!s)", pdf))


def test_genera_pdf_de_dos_paginas_sin_dejar_part(tmp_path):
    a = _imagen(tmp_path / "001.jpg", (255, 0, 0))
    b = _imagen(tmp_path / "002.png", (0, 0, 255))
    salida = str(tmp_path / "out.pdf")
    assert pdf_worker.generar_pdf([a, b], salida, lambda m: None)
    with open(salida, "rb") as f:
        datos = f.read()
    assert datos.startswith(b"%PDF")
    assert _paginas(datos) == 2
    assert not os.path.exists(salida + ".part")


def test_convierte_webp_y_transparencia(tmp_path):
    a = _imagen(tmp_path / "001.webp", (0, 255, 0))
    b = _imagen(tmp_path / "002.png", (0, 0, 0, 0), modo="RGBA")
    salida = str(tmp_path / "out.pdf")
    assert pdf_worker.generar_pdf([a, b], salida, lambda m: None)
    with open(salida, "rb") as f:
        assert _paginas(f.read()) == 2
    assert not os.path.exists(salida + ".part")


def test_fallo_no_deja_pdf_ni_part(tmp_path):
    falsa = tmp_path / "001.jpg"
    falsa.write_bytes(b"<html>no soy una imagen</html>")
    salida = str(tmp_path / "out.pdf")
    logs = []
    assert not pdf_worker.generar_pdf([str(falsa)], salida, logs.append)
    assert not os.path.exists(salida)
    assert not os.path.exists(salida + ".part")
    assert any("[ERROR]" in l for l in logs)


def test_fallo_no_pisa_un_pdf_existente(tmp_path):
    salida = tmp_path / "out.pdf"
    salida.write_bytes(b"%PDF anterior")
    falsa = tmp_path / "001.jpg"
    falsa.write_bytes(b"nada")
    assert not pdf_worker.generar_pdf([str(falsa)], str(salida), lambda m: None)
    assert salida.read_bytes() == b"%PDF anterior"
