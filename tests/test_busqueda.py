"""Funciones puras de core.busqueda: idioma, título base y parecido."""
import pytest

from core import busqueda


@pytest.mark.parametrize("titulo", [
    "Spanking Time",
    "Despair of the Princess",
    "Revenge of the Nerds",
    "Raw Desire",
])
def test_idioma_de_palabras_que_no_son_idiomas(titulo):
    assert busqueda.idioma_de(titulo, "otro") is None


@pytest.mark.parametrize("titulo,codigo", [
    ("[Artista] Obra [Spanish]", "es"),
    ("Obra (Español)", "es"),
    ("Obra (ENG)", "en"),
    ("[Circle] Work [English] [Digital]", "en"),
    ("Obra [RAW]", "ja"),
    ("Obra [Chinese] [汉化组]", "zh"),
])
def test_idioma_de_etiquetas(titulo, codigo):
    assert busqueda.idioma_de(titulo, "otro") == codigo


def test_idioma_de_pista_y_sitio():
    assert busqueda.idioma_de("Lo que sea", "hitomi", pista="Japanese") == "ja"
    assert busqueda.idioma_de("Sin etiqueta", "tmohentai.app") == "es"
    assert busqueda.idioma_de("ふたりの夏", "otro") == "ja"


def test_titulo_base():
    assert busqueda.titulo_base("The Wife-Lenders 1") == ("the wife lenders", 1.0)
    assert busqueda.titulo_base("Obra Parte 2") == ("obra", 2.0)
    assert busqueda.titulo_base("[Artista] Obra Sin Número") == ("obra sin numero", None)
    assert busqueda.titulo_base("1") == ("1", None)  # un número solo no es "parte"
    # Partes con decimales: antes "Parte 2.5" salía como parte 5 de "obra parte 2"
    assert busqueda.titulo_base("Obra Parte 2.5") == ("obra", 2.5)
    assert busqueda.titulo_base("Obra 1.5") == ("obra", 1.5)


def test_parecido():
    assert busqueda.parecido("wife lenders", "[Artista] The Wife-Lenders 2 [English]") >= 1.0
    assert busqueda.parecido("babi", "[Hanamori Banbi (Babi)] Obra") > 0
    assert busqueda.parecido("otra cosa", "The Wife-Lenders") == 0.0
    assert busqueda.parecido("", "lo que sea") == 0.0
