"""
Agrupa resultados de búsqueda en SERIES y PARTES con DeepSeek.

Problema: las partes de una serie se titulan muy distinto entre sitios y traducciones
("Oharami-sama Ni Kago | The Oharami maiden: Two vessels", "Ohara Misama 2 Ni Rou",
"The Oharami Maiden Part 2", "Oharami-Sama Second Story 1-2"...). Ninguna búsqueda por
palabras las une, y el mismo capítulo aparece repetido en varios sitios.

Enfoque:
  1. Candidatos: búsqueda por el texto del usuario + búsqueda por el ARTISTA (lo que de verdad
     une una serie). El artista sale de los títulos "[Círculo (Artista)]" o de la ficha.
  2. DeepSeek recibe la lista numerada (sitio, idioma, título) y devuelve JSON con las series,
     sus partes en orden y qué números pertenecen a cada parte. No puede inventar obras: el bot
     descarta cualquier número que no exista en la lista.
  3. Si DeepSeek falla, no responde o no hay clave, se devuelve None y el bot muestra la lista
     normal.

Configuración: DEEPSEEK_API_KEY (en el .env del bot), DEEPSEEK_MODEL (por defecto deepseek-flash).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional

import aiohttp

from . import busqueda

API_URL = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions")
MODELO = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")  # nombre actual (V4.1-Flash-1017); "deepseek-v4-flash" solo se redirige
MAX_CANDIDATOS = 70

PROMPT = """Eres un catalogador de doujinshi y manga. Recibes una lista numerada de resultados de búsqueda de
varios sitios. Cada línea: "<n> | <sitio> | <idioma> | <título>". Los títulos de UNA MISMA obra varían mucho
entre sitios y traducciones (romanizaciones, traducción al inglés, espacios distintos, "Part 2", "Second Story",
"2 Ni Rou", "Two vessels"...). El mismo capítulo suele aparecer repetido en varios sitios e idiomas.

Tarea: agrupa los resultados en SERIES (obras relacionadas: mismas protagonistas/historia, continuaciones,
spin-offs) y, dentro de cada serie, en PARTES (capítulo/volumen). Ordena las partes cronológicamente
(1, 2, 3, ... y los extras al final). Una "parte" reúne todas las versiones (sitios/idiomas) del mismo capítulo.
Una recopilación de varias partes ("1-2", "Complete") es una parte propia con nombre descriptivo.

Reglas:
- Usa SOLO los números de la lista. No inventes obras ni partes que no estén.
- Pon primero la serie que mejor corresponde a la búsqueda del usuario.
- Omite resultados que no tengan relación con ninguna serie de la búsqueda (no los incluyas).
- "nombre" de la serie: el título romanizado más reconocible. "parte": texto corto ("1", "2", "3", "Extra", "1-2").
Devuelve SOLO JSON: {"series":[{"nombre":"...","artista":"...","partes":[{"parte":"1","items":[3,7,12]}]}]}"""


@dataclass
class Parte:
    nombre: str
    resultados: List["busqueda.Resultado"] = field(default_factory=list)


@dataclass
class Serie:
    nombre: str
    artista: str = ""
    partes: List[Parte] = field(default_factory=list)


def artista_probable(resultados: List["busqueda.Resultado"]) -> Optional[str]:
    """El artista más repetido en los títulos ("[Círculo (Artista)]") de los mejores resultados."""
    nombres = [busqueda.autor_desde_titulo(r.titulo) for r in resultados[:25]]
    nombres = [n for n in nombres if n and len(n) >= 2]
    if not nombres:
        return None
    nombre, veces = Counter(n.lower() for n in nombres).most_common(1)[0]
    return next(n for n in nombres if n.lower() == nombre) if veces >= 2 or len(nombres) == 1 else None


async def candidatos(texto: str, artista: Optional[str] = None) -> tuple:
    """(resultados por texto ∪ obras del artista, artista usado). Sin duplicados de URL."""
    por_texto = await busqueda.buscar(texto, limite=50)
    artista = artista or artista_probable(por_texto)
    if artista and busqueda.normalizar(artista) not in busqueda.normalizar(texto):
        por_artista = await busqueda.buscar(artista, limite=50)
    else:
        por_artista = []
    vistos, todos = set(), []
    for r in por_texto + por_artista:
        if r.url not in vistos:
            vistos.add(r.url)
            todos.append(r)
    return todos[:MAX_CANDIDATOS], artista


async def _deepseek(mensaje_usuario: str) -> Optional[dict]:
    clave = os.getenv("DEEPSEEK_API_KEY", "")
    if not clave:
        return None
    payload = {
        "model": MODELO,
        "messages": [{"role": "system", "content": PROMPT}, {"role": "user", "content": mensaje_usuario}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    cab = {"Authorization": f"Bearer {clave}", "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45)) as s:
            async with s.post(API_URL, json=payload, headers=cab) as r:
                if r.status != 200:
                    print(f"[WARN] DeepSeek respondió {r.status}")
                    return None
                datos = await r.json()
        contenido = datos["choices"][0]["message"]["content"].strip()
        contenido = re.sub(r"^```(?:json)?|```$", "", contenido).strip()
        return json.loads(contenido)
    except Exception as e:
        print(f"[WARN] DeepSeek falló: {e}")
        return None


async def agrupar(texto: str, resultados: List["busqueda.Resultado"],
                  actual: Optional[str] = None) -> Optional[List[Serie]]:
    """Series y partes a partir de los resultados. actual: título de lo recién descargado (para
    que DeepSeek ponga primero su serie). None si no se pudo agrupar."""
    if len(resultados) < 2:
        return None
    lineas = [f"{i} | {r.sitio} | {r.idioma or '?'} | {r.titulo[:160]}" for i, r in enumerate(resultados)]
    pedido = f"Búsqueda del usuario: {texto}\n"
    if actual:
        pedido += f"Obra que el usuario acaba de descargar (su serie va primero): {actual}\n"
    pedido += "\nResultados:\n" + "\n".join(lineas)
    datos = await _deepseek(pedido)
    if not datos or not isinstance(datos.get("series"), list):
        return None

    series, usados = [], set()
    for s in datos["series"][:6]:
        serie = Serie(str(s.get("nombre") or "?")[:120], str(s.get("artista") or "")[:80])
        for p in s.get("partes") or []:
            items = []
            for i in p.get("items") or []:
                # Validación: solo números que existen y no repetidos (DeepSeek no puede inventar)
                if isinstance(i, int) and 0 <= i < len(resultados) and i not in usados:
                    usados.add(i)
                    items.append(resultados[i])
            if items:
                serie.partes.append(Parte(str(p.get("parte") or "?")[:40], busqueda.ordenar_por_idioma(items)))
        if serie.partes:
            series.append(serie)
    return series or None


async def series_para(texto: str, actual: Optional[str] = None, artista: Optional[str] = None):
    """Todo el flujo: candidatos + agrupación. Devuelve (series o None, candidatos, artista)."""
    lista, artista = await candidatos(texto, artista)
    series = await agrupar(texto, lista, actual) if lista else None
    return series, lista, artista
