"""
Module: bot_gofile
Description: Entrega de archivos del bot de Discord.

  - Subida a GoFile (cuenta de invitado + carpeta pública) cuando un archivo supera el
    límite de adjuntos de Discord.
  - Índice "enlace de GoFile -> PDF local" (para !traducir <enlace>).
  - Índice "id corto -> PDF local" del botón persistente "Traducir al español".
  - _entregar_pdf: adjunto de Discord o enlace de GoFile.
"""
import asyncio
import hashlib
import json
import mimetypes
import os
from typing import Optional

import aiohttp
import discord

import core.config

_gofile_token: Optional[str] = None
_gofile_root: Optional[str] = None
_gofile_public_folder: Optional[str] = None
# Evita que dos primeras subidas simultáneas creen dos cuentas/carpetas de invitado
_gofile_lock = asyncio.Lock()
GOFILE_SUBIDA_MAX_SEG = 1800  # un PDF de cientos de MB por la subida del NAS
_GOFILE_TIMEOUT_API = aiohttp.ClientTimeout(total=60, sock_connect=15)
_GOFILE_TIMEOUT_SUBIDA = aiohttp.ClientTimeout(total=GOFILE_SUBIDA_MAX_SEG, sock_connect=30, sock_read=300)

# Límite de adjuntos: el del servidor (sube con las mejoras) menos un margen para la cabecera
# multipart; en mensajes directos no hay guild y se usa el mínimo histórico de Discord.
LIMITE_ADJUNTO_DM = 8 * 1024 * 1024
MARGEN_ADJUNTO = 256 * 1024


def _gofile_reset() -> None:
    """Olvida la cuenta de invitado: la siguiente subida crea una nueva (token caducado, 401/403...)."""
    global _gofile_token, _gofile_root, _gofile_public_folder
    _gofile_token = _gofile_root = _gofile_public_folder = None


async def _ensure_gofile_account() -> bool:
    """Creates/reuses a GoFile guest account token (the API now requires auth)."""
    global _gofile_token, _gofile_root
    if _gofile_token:
        return True
    async with _gofile_lock:
        if _gofile_token:  # otra subida la creó mientras se esperaba el lock
            return True
        try:
            async with aiohttp.ClientSession(timeout=_GOFILE_TIMEOUT_API) as session:
                async with session.post('https://api.gofile.io/accounts', json={}) as resp:
                    data = await resp.json()
                    if resp.status == 200 and data.get('status') == 'ok':
                        _gofile_token = data['data'].get('token')
                        _gofile_root = data['data'].get('rootFolder')
                        return bool(_gofile_token)
        except Exception as e:
            print(f"[ERROR GOFILE] account: {e}")
    return False


async def _ensure_gofile_public_folder() -> Optional[str]:
    """Ensures a public folder exists in the GoFile account and returns its id.

    Files must live inside a *public* folder for their download page to be
    shareable; by default content is private ("not publicly accessible").
    """
    global _gofile_public_folder
    if _gofile_public_folder:
        return _gofile_public_folder
    if not await _ensure_gofile_account():
        return None
    async with _gofile_lock:
        if _gofile_public_folder:
            return _gofile_public_folder
        try:
            headers = {'Authorization': f'Bearer {_gofile_token}'}
            async with aiohttp.ClientSession(timeout=_GOFILE_TIMEOUT_API) as session:
                payload = {
                    'parentFolderId': _gofile_root,
                    'folderName': 'manga',
                    'public': True,
                }
                async with session.post(
                    'https://api.gofile.io/contents/createFolder',
                    json=payload,
                    headers=headers,
                ) as resp:
                    if resp.status in (401, 403):
                        _gofile_reset()
                        return None
                    data = await resp.json()
                    if resp.status == 200 and data.get('status') == 'ok':
                        _gofile_public_folder = data['data'].get('id')
        except Exception as e:
            print(f"[ERROR GOFILE] public folder: {e}")
    return _gofile_public_folder


async def upload_to_gofile(file_path: str) -> Optional[str]:
    """Uploads a file to GoFile and returns the download link."""
    try:
        if not await _ensure_gofile_account():
            return None
        headers = {'Authorization': f'Bearer {_gofile_token}'}
        folder_id = await _ensure_gofile_public_folder() or _gofile_root
        async with aiohttp.ClientSession(timeout=_GOFILE_TIMEOUT_SUBIDA) as session:
            async with session.get('https://api.gofile.io/servers', headers=headers,
                                   timeout=_GOFILE_TIMEOUT_API) as resp:
                if resp.status in (401, 403):
                    _gofile_reset()
                    return None
                if resp.status != 200: return None
                data = await resp.json()
                if data.get('status') != 'ok': return None

                server = data['data']['servers'][0]['name']
                upload_url = f'https://{server}.gofile.io/uploadFile'

            filename = os.path.basename(file_path)
            extension = os.path.splitext(file_path)[1].lower()
            ascii_filename = filename.encode('ascii', 'ignore').decode('ascii').replace(" ", "_")
            # Strip leading punctuation/underscores left by removed non-ASCII chars
            ascii_filename = ascii_filename.lstrip('._- ')
            if not ascii_filename or "." not in ascii_filename:
                # Un título solo con caracteres no ASCII dejaría un nombre sin extensión
                ascii_filename = (ascii_filename or "archivo") + extension

            tipo = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            with open(file_path, 'rb') as f:
                form_data = aiohttp.FormData(quote_fields=False)
                form_data.add_field('file', f, filename=ascii_filename, content_type=tipo)
                if folder_id:
                    form_data.add_field('folderId', folder_id)

                async with session.post(upload_url, data=form_data, headers=headers) as upload_resp:
                    if upload_resp.status == 200:
                        res = await upload_resp.json()
                        if res.get('status') == 'ok':
                            return res['data']['downloadPage']
                    # Token o carpeta inválidos (cuenta de invitado caducada): se recrean en la próxima subida
                    print(f"[ERROR GOFILE] upload HTTP {upload_resp.status}")
                    _gofile_reset()
    except Exception as e:
        print(f"[ERROR GOFILE] {e}")
    return None


# ─── Índices en PDF/ ──────────────────────────────────────────────────────────
# Índice "enlace de GoFile -> PDF local", para que !traducir <enlace> use la copia
# del NAS en vez de volver a descargar de GoFile lo que el propio bot subió.
_GOFILE_INDEX = os.path.join(os.getcwd(), core.config.PDF_FOLDER_NAME, ".gofile_index.json")
# Índice "id corto -> PDF local" del botón persistente «Traducir» (el custom_id lleva el id)
_TRADUCIR_INDEX = os.path.join(os.getcwd(), core.config.PDF_FOLDER_NAME, ".traducir_index.json")


def _leer_indice(ruta: str) -> dict:
    try:
        with open(ruta, encoding="utf-8") as f:
            datos = json.load(f)
        return datos if isinstance(datos, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}  # no existe o está corrupto (p. ej. escritura cortada): se empieza de nuevo


def _escribir_indice(ruta: str, datos: dict) -> None:
    # Escritura atómica: un corte a mitad no deja el JSON a medias
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ruta)


def _registrar_gofile(link: str, file_path: str) -> None:
    try:
        datos = _leer_indice(_GOFILE_INDEX)
        datos[link.rstrip("/")] = os.path.abspath(file_path)
        _escribir_indice(_GOFILE_INDEX, datos)
    except Exception as e:
        print(f"[WARN] No se pudo registrar el enlace de GoFile: {e}")


def _pdf_de_gofile(link: str) -> Optional[str]:
    ruta = _leer_indice(_GOFILE_INDEX).get(link.rstrip("/"))
    return ruta if ruta and os.path.exists(ruta) else None


def registrar_pdf_traducible(file_path: str) -> str:
    """Id corto (hash de la ruta) para el custom_id del botón «Traducir»; lo guarda en el índice."""
    ruta = os.path.abspath(file_path)
    pdf_id = hashlib.sha1(ruta.encode("utf-8")).hexdigest()[:16]
    try:
        datos = _leer_indice(_TRADUCIR_INDEX)
        if datos.get(pdf_id) != ruta:
            # De paso se olvidan los PDF que ya no están (borrados a mano en el NAS)
            datos = {k: v for k, v in datos.items() if os.path.exists(v)}
            datos[pdf_id] = ruta
            _escribir_indice(_TRADUCIR_INDEX, datos)
    except Exception as e:
        print(f"[WARN] No se pudo registrar el PDF para traducir: {e}")
    return pdf_id


def pdf_traducible(pdf_id: str) -> Optional[str]:
    """Ruta local del PDF de un botón «Traducir», o None si ya no existe."""
    ruta = _leer_indice(_TRADUCIR_INDEX).get(pdf_id)
    return ruta if ruta and os.path.exists(ruta) else None


def limite_adjunto(channel) -> int:
    """Bytes que se pueden adjuntar en `channel` con margen de seguridad."""
    guild = getattr(channel, "guild", None)
    limite = getattr(guild, "filesize_limit", None) or LIMITE_ADJUNTO_DM
    return limite - MARGEN_ADJUNTO


async def _entregar_pdf(channel, file_path: str, view: Optional[discord.ui.View] = None) -> Optional[str]:
    """Envía un PDF por Discord, o por GoFile si supera el límite. Devuelve el enlace de GoFile si lo hubo."""
    filename = os.path.basename(file_path)
    tam = os.path.getsize(file_path)
    size_mb = tam / (1024 * 1024)
    if tam > limite_adjunto(channel):
        await channel.send(f"[WARN] `{filename}` ({size_mb:.2f}MB) exceeds limit. Uploading to GoFile...")
        link = await upload_to_gofile(file_path)
        if link:
            # Solo los PDF que se quedan en la carpeta del bot (las traducciones son temporales)
            if os.path.abspath(file_path).startswith(os.path.dirname(_GOFILE_INDEX)):
                _registrar_gofile(link, file_path)
            if view is not None:
                await channel.send(f"[SUCCESS] **{filename}**: {link}", view=view)
            else:
                await channel.send(f"[SUCCESS] **{filename}**: {link}")
        else:
            await channel.send(f"[ERROR] Failed to upload `{filename}` to GoFile.")
        return link
    if view is not None:
        await channel.send(file=discord.File(file_path), view=view)
    else:
        await channel.send(file=discord.File(file_path))
    return None
