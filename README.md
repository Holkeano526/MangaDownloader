# MangaDownloader

Descarga mangas y doujinshi de **11 sitios** a PDF, **busca en todos a la vez**, agrupa las partes de una
serie con IA (DeepSeek) y, con un botón, **los traduce** con [MangaScan AI](https://github.com/Holkeano526/mangascan).

Se usa sobre todo como **bot de Discord** (corre en un NAS con Docker), pero el mismo núcleo (`core/`) sirve
también a una interfaz web (Next.js + FastAPI) y a una app de escritorio (Tkinter).

## Cómo funciona

<p align="center">
  <img src="docs/flujo_app.drawio.svg" alt="Flujo de MangaDownloader" width="100%">
</p>

> El diagrama es un `.drawio.svg`: se ve en GitHub y se edita abriéndolo con draw.io
> (fuente: [`docs/flujo_app.drawio`](docs/flujo_app.drawio)).

1. **Descarga.** `core/handler.py` elige el primer handler cuyo dominio aparece en el *hostname* de la URL
   (no en cualquier parte de la URL, para evitar SSRF). El handler obtiene el título y la lista de páginas,
   casi siempre por HTTP puro; Chromium (Playwright) queda para los sitios que lo necesitan o como respaldo.
   `core/utils.descargar_imagenes()` baja las páginas en paralelo con reintentos y convierte WebP/AVIF a JPEG
   mientras llegan; luego `img2pdf` arma el PDF.
2. **Entrega.** Si el PDF pesa ≤ 7,9 MB se adjunta en Discord; si no, se sube a **GoFile** y se apunta en
   `PDF/.gofile_index.json`. Junto al PDF aparece el botón **🌐 Traducir**.
3. **Autor y continuaciones.** Tras cada descarga el bot busca el autor y las otras partes de la serie en
   todos los sitios, y las muestra agrupadas (series › partes › fuentes por idioma) con un menú para
   descargarlas.
4. **Traducción.** El bot sube el PDF local a MangaScan por la LAN (sin el límite de Discord), sigue el
   progreso y devuelve el PDF traducido por Discord o GoFile.

## Bot de Discord

| Comando | Qué hace |
|---|---|
| `!descargar <url>` | Descarga a PDF. Acepta una ficha, un lector o una serie (pone sus capítulos en cola). |
| `!buscar <título>` | Busca en los 11 sitios, suma las obras del artista y agrupa con DeepSeek en series › partes › fuentes (🇪🇸 primero, luego 🇬🇧). Si la IA falla, muestra una lista paginada de tarjetas con portada. Funciona sin número de parte: `!buscar The Wife-Lenders`. |
| `!traducir <enlace GoFile \| nombre.pdf>` | Traduce con MangaScan un PDF que el bot ya descargó (por su enlace de GoFile o su nombre). También acepta un PDF adjunto al mensaje. |
| `!help` | Ayuda. |

**Botones:** ⏹️ Cancelar (descarga y traducción; no deja PDFs a medias), 🌐 Traducir (en cada PDF entregado),
menú *Descargar una parte* y 📋 *Ver todos los resultados* en las búsquedas.

## Sitios compatibles

| Sitio | Descarga | Búsqueda | Cómo |
|---|:-:|:-:|---|
| hitomi.la | ✅ | ✅ | HTTP: `galleries.js` + `gg.js`; búsqueda en su índice binario (B-tree + `.nozomi`) |
| hentainexus.com | ✅ | ✅ | HTTP: descifra la lista de páginas de `initReader` (respaldo con navegador) |
| nhentai.to · .xxx · .website | ✅ | ✅ | HTTP, handler genérico para los clones de nhentai |
| 3hentai.net | ✅ | ✅ | HTTP (mismo handler genérico; verifica el nº real de páginas) |
| tmohentai.app | ✅ | ✅ | HTTP |
| lectorhentai.com | ✅ | ✅ | HTTP |
| hentai2read.com | ✅ | ✅ | HTTP: JSON de Inertia |
| m440.in | ✅ | ✅ | Navegador (crawl4ai / Playwright) |
| hentalk.pw (y enlaces fakku.cc) | ✅ | ✅ | Navegador |
| shademanga | ✅ | — | Navegador |
| nhentai.net · nhentai.top | ⚠️ | — | Protegidos por Cloudflare: el bot avisa y sugiere un espejo. **No se intenta saltar la protección.** |

## Configuración

Variables de entorno (en Docker van en el `.env` del servicio):

| Variable | Por defecto | Uso |
|---|---|---|
| `DISCORD_TOKEN` | — | Token del bot (obligatorio). |
| `DEEPSEEK_API_KEY` | — | Agrupación de series con IA. Sin clave, `!buscar` muestra la lista normal. |
| `DEEPSEEK_MODEL` | `deepseek-flash` | Modelo de DeepSeek. |
| `DEEPSEEK_API_URL` | `https://api.deepseek.com/chat/completions` | Endpoint compatible con OpenAI. |
| `MANGASCAN_URL` | — | URL de MangaScan AI en la LAN (p. ej. `http://192.168.1.x:8001`). |
| `CONCURRENCIA_IMAGENES` | `3` | Conexiones simultáneas por descarga. |
| `JPEG_CALIDAD` | `70` | Calidad al convertir WebP/AVIF a JPEG. |
| `HEADLESS` | `true` | Navegador sin ventana (Docker). |

## Instalación

### Docker (NAS)

El bot corre como servicio `discord_bot`. La imagen parte de una base congelada
(`manga-downloader-discord_bot:base-20260816`) porque `requirements.txt` no fija versiones y un build desde
cero puede romperse; los cambios de código se añaden como una capa fina con `Dockerfile.bot`:

```bash
docker build -f Dockerfile.bot -t manga-downloader-discord_bot:latest .
docker compose up -d --no-build --force-recreate discord_bot
```

Antes de reiniciar el bot, comprobar que no haya una descarga ni una traducción en curso.

### Local

```bash
pip install -r requirements.txt
playwright install chromium
python bot.py          # bot de Discord (requiere .env con DISCORD_TOKEN)
python app.py          # escritorio
START_WEB_VERSION.bat  # web: FastAPI :8000 + Next.js :3000 (Windows)
```

## Estructura

```
bot.py               Bot de Discord: comandos, vistas (botones/menús), entrega y traducción
mangascan_client.py  Cliente HTTP de MangaScan AI (subida, progreso, cancelar)
app.py               App de escritorio (Tkinter)
web_server.py        API FastAPI + WebSocket para web_client_next/
core/
  handler.py         Enrutador URL → handler (el orden importa)
  utils.py           Motor de descarga, conversión de imágenes y PDF
  busqueda.py        Búsqueda en todos los sitios, idioma, autor, continuaciones
  agrupador.py       Agrupación en series/partes con DeepSeek
  config.py          Constantes (User-Agent, carpetas)
  sites/             Un handler por sitio (BaseSiteHandler)
docs/                Diagrama de flujo (draw.io)
```

## Rendimiento

Medido en el NAS (contenedor limitado a 1–2 CPU):

| Prueba | Resultado |
|---|---|
| nhentai.to, 80 páginas | 11,5 s, 0 errores |
| hentainexus, 41 páginas | 4,8 s |

Claves: lista de páginas por HTTP en vez de navegador, pool paralelo con reintentos ante 429/5xx, conversión de
imágenes mientras se descarga (hilos = CPU que permite el cgroup, para evitar *throttling*).

## Añadir un sitio

Ver [HOW_TO_ADD_SITES.md](HOW_TO_ADD_SITES.md).

## Límites

- Los sitios con Cloudflare/anti-bot (nhentai.net, nhentai.top) no se descargan: se avisa al usuario.
- Los sitios cambian su HTML; si un handler HTTP deja de funcionar, los que tienen respaldo pasan al navegador
  y el resto da un error claro en el log.
- La agrupación con IA depende de DeepSeek; en horas punta (UTC 01–04 y 06–10, lun–vie) el precio es ×2.
