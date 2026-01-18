# Universal Manga PDF Downloader 📥

Una herramienta todo-en-uno para descargar manga y doujinshi de sitios populares y convertirlos automáticamente a PDF.

## 🚀 Sitios Soportados
| Sitio | Método de Descarga | Notas |
|-------|--------------------|-------|
| **TMOHentai** | IA + Regex | Prioriza calidad original. |
| **M440.in** | Crawler Simple | Soporta portadas (baja todos los capítulos) y capítulos sueltos. |
| **Hentai2Read** | Extracción JSON | Rápido y eficiente. |
| **Hitomi.la** | **Playwright** (Navegador) | ✅ Bypassea protección 404.<br>✅ Descarga imágenes FULL RES.<br>✅ Usa ventanas visibles para evitar bloqueos. |
| **nhentai.net** | **Playwright** (API) | ✅ Bypassea Cloudflare.<br>✅ Descarga calidad original. |

## 🛠️ Requisitos Previos
Necesitas tener instalado **Python 3.8+** y las siguientes dependencias:

1.  **Instalar librerías de Python:**
    ```powershell
    pip install aiohttp pillow pandas playwright crawl4ai
    ```
    *(Nota: `crawl4ai` es opcional si solo usas Hitomi/nhentai, pero necesario para TMO)*

2.  **Instalar Navegadores de Playwright:**
    Necesario para Hitomi y nhentai.
    ```powershell
    playwright install chromium
    ```

3.  **Configuración de API (Solo para TMO):**
    Si usas TMO, el script busca una API Key de Gemini en el código (`os.environ["GOOGLE_API_KEY"]`). Asegúrate de que sea válida.

## 📖 Cómo Usar

1.  **Ejecutar el script:**
    ```powershell
    python tmo.py
    ```

2.  **Interfaz Gráfica:**
    Se abrirá una ventana sencilla.
    *   **Input:** Pega la URL del manga/capítulo.
        *   *Ejemplo Hitomi:* `https://hitomi.la/reader/12345.html` o `https://hitomi.la/doujinshi/...`
        *   *Ejemplo nhentai:* `https://nhentai.net/g/622745/`
    *   **Logs:** Verás el progreso detallado en la parte inferior (y en la consola negra que se abre detrás).

3.  **Resultados:**
    *   El script descargará las imágenes en una carpeta temporal.
    *   Generará un **PDF** en la carpeta `PDF/`.
    *   Al finalizar, abrirá automáticamente el PDF o la carpeta.

## ⚠️ Solución de Problemas

*   **Error "Playwright... not found":** Ejecuta `pip install playwright` y luego `playwright install chromium`.
*   **Ventana del navegador se abre sola:** Es normal. Hitomi y nhentai requieren un navegador real para validar que eres humano. **No lo cierres** mientras descarga.
*   **Error 404 en imágenes:** Asegúrate de tener la última versión del script, ya que incluye correcciones de `Referer` y tokens de seguridad (`gg.js`).

---
*Desarrollado con ayuda de Gemini*
# MangaDownloader
# MangaDownloader
