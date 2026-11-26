"""
Module: mangascan_client
Description: Cliente asíncrono de la API de MangaScan AI (traductor de manga del NAS).

Sube un PDF a MangaScan, sigue el progreso por su stream de log (SSE) y descarga
el PDF traducido. Lo usa el comando !traducir y el botón "Traducir" del bot.

Los PDF que descarga el bot ya están en el NAS, así que se suben por la red local
(sin límite de tamaño). GoFile solo se usa para ENTREGAR el resultado al usuario
cuando supera el límite de adjuntos de Discord.

Configuración: MANGASCAN_URL (por defecto http://192.168.1.86:8001).
"""
from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

import aiohttp

MANGASCAN_URL = os.getenv("MANGASCAN_URL", "http://192.168.1.86:8001").rstrip("/")

# Una traducción larga puede durar más de una hora: sin límite total, pero sí de conexión.
_TIMEOUT_STREAM = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=None)
_TIMEOUT_CORTO = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=600)

_RE_PROGRESO = re.compile(r"Progreso: (\d+)/(\d+) \| ([\d.]+) s/página")
_RE_COLA = re.compile(r"Posición en cola: (\d+)")
_RE_FIN = re.compile(r"Procesamiento finalizado con codigo: (-?\d+)")
_RE_PAGINAS = re.compile(r"PDF abierto: .*\((\d+) páginas\)")


@dataclass
class EstadoTraduccion:
    """Lo que el bot muestra en el embed mientras se traduce."""
    task_id: str = ""
    fase: str = "Subiendo a MangaScan..."
    pagina: int = 0
    total: int = 0
    seg_por_pagina: float = 0.0
    cola: int = 0
    codigo_salida: Optional[int] = None
    errores: list[str] = field(default_factory=list)


Callback = Callable[[EstadoTraduccion], Awaitable[None]]


async def disponible() -> bool:
    """True si la web de MangaScan responde."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
            async with s.get(f"{MANGASCAN_URL}/api/jobs") as r:
                return r.status == 200
    except Exception:
        return False


async def traducir_pdf(ruta_pdf: str, carpeta_salida: str, on_estado: Callback) -> tuple[EstadoTraduccion, Optional[str]]:
    """Sube ruta_pdf a MangaScan, espera a que termine y descarga el resultado.

    Devuelve (estado final, ruta del PDF traducido o None si falló).
    on_estado se llama en cada cambio relevante (el llamador decide cada cuánto editar Discord).
    """
    estado = EstadoTraduccion()
    nombre = os.path.basename(ruta_pdf)

    async with aiohttp.ClientSession(timeout=_TIMEOUT_CORTO) as s:
        # 1) Subir (multipart en streaming: no carga el PDF entero en memoria)
        with open(ruta_pdf, "rb") as f:
            # quote_fields=False: sin esto aiohttp manda el nombre japonés/coreano
            # codificado como URL (%E4%BF%AE...) y así llegaba a la Biblioteca.
            form = aiohttp.FormData(quote_fields=False)
            form.add_field("file", f, filename=nombre, content_type="application/pdf")
            form.add_field("fast_mode", "false")
            async with s.post(f"{MANGASCAN_URL}/api/upload", data=form) as r:
                if r.status != 200:
                    estado.errores.append(f"MangaScan rechazó la subida (HTTP {r.status})")
                    return estado, None
                datos = await r.json()
        estado.task_id = datos["task_id"]
        nombre_servidor = datos["filename"]
        estado.fase = "En cola de MangaScan..."
        await on_estado(estado)

        # 2) Seguir el log en vivo (Server-Sent Events) hasta el final
        async with s.get(f"{MANGASCAN_URL}/api/stream/{estado.task_id}", timeout=_TIMEOUT_STREAM) as r:
            async for bruto in r.content:
                linea = bruto.decode("utf-8", "replace").strip()
                if not linea.startswith("data:"):
                    continue
                linea = linea[5:].strip()
                if m := _RE_COLA.search(linea):
                    estado.cola = int(m.group(1))
                    estado.fase = f"En cola (posición {estado.cola})..."
                    await on_estado(estado)
                elif "Iniciando procesamiento" in linea:
                    estado.fase = "Extrayendo páginas..."
                    await on_estado(estado)
                elif m := _RE_PAGINAS.search(linea):
                    estado.total = int(m.group(1))
                elif m := _RE_PROGRESO.search(linea):
                    estado.pagina, estado.total = int(m.group(1)), int(m.group(2))
                    estado.seg_por_pagina = float(m.group(3))
                    estado.fase = "Traduciendo..."
                    await on_estado(estado)
                elif "Recomponiendo" in linea:
                    estado.fase = "Generando el PDF..."
                    await on_estado(estado)
                elif "✗" in linea or "[ERROR]" in linea:
                    estado.errores.append(linea[-200:])
                if m := _RE_FIN.search(linea):
                    estado.codigo_salida = int(m.group(1))
                    break
                if "cancelado por el usuario" in linea:
                    estado.fase = "Cancelada"
                    estado.codigo_salida = -1
                    break

        if estado.codigo_salida != 0:
            estado.fase = "Falló" if estado.fase != "Cancelada" else estado.fase
            return estado, None

        # 3) Descargar el PDF traducido
        os.makedirs(carpeta_salida, exist_ok=True)
        destino = os.path.join(carpeta_salida, f"traducido_{nombre_servidor}")
        params = {"filename": nombre_servidor}
        async with s.get(f"{MANGASCAN_URL}/api/download/{estado.task_id}", params=params) as r:
            if r.status != 200 or r.content_type != "application/pdf":
                estado.errores.append("MangaScan terminó pero no se pudo descargar el PDF")
                return estado, None
            with open(destino, "wb") as out:
                async for trozo in r.content.iter_chunked(1024 * 1024):
                    out.write(trozo)
        estado.fase = "Terminada"
        return estado, destino


async def cancelar(task_id: str, borrar: bool = True) -> bool:
    """Cancela una traducción en MangaScan y, si borrar, quita sus restos de la Biblioteca."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.post(f"{MANGASCAN_URL}/api/cancel/{task_id}") as r:
                ok = r.status == 200
            if borrar:
                await asyncio.sleep(2)  # que el proceso termine antes de borrar su carpeta
                async with s.delete(f"{MANGASCAN_URL}/api/jobs/{task_id}") as r:
                    pass
            return ok
    except Exception:
        return False
