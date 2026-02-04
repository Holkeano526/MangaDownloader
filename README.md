# Universal Manga PDF Downloader 📥

Una herramienta todo-en-uno para descargar manga, doujinshi y cómics desde sitios populares de forma automatizada y convertirlos a PDF de alta calidad.

## 🚀 Características
*   **Multi-Plataforma:** Soporta TMO, ZonaTMO, M440, H2R, Hitomi y nhentai.
*   **Modo Dual:**
    *   🖥️ **App de Escritorio:** Interfaz gráfica simple y rápida.
    *   🤖 **Bot de Discord:** Descarga remota con subida automática a Discord o GoFile.
*   **PDF Automático:** Convierte todas las imágenes descargadas en un único archivo PDF.
*   **Bypasses:** Salta protecciones Cloudflare y 403 mediante Playwright y headers inteligentes.

## 🌐 Sitios Soportados
| Sitio | Método | Notas |
|-------|--------|-------|
| **ZonaTMO** | Crawler + Cascade | ✅ Soporta series completas (baja todos los caps uno a uno) y capítulos sueltos. |
| **TMOHentai** | IA + Regex | Prioriza calidad original. |
| **M440.in** | Crawler Simple | Soporta portadas y capítulos sueltos. |
| **Hentai2Read** | Extracción JSON | Rápido y eficiente. |
| **Hitomi.la** | **Playwright** | ✅ Bypassea protección 404.<br>✅ Descarga imágenes FULL RES. |
| **nhentai.net** | **Playwright** API | ✅ Bypassea Cloudflare.<br>✅ Descarga calidad original. |

## 🛠️ Instalación

1.  **Clonar el repositorio:**
    ```bash
    git clone https://github.com/Holkeano526/MangaDownloader.git
    cd manga-downloader
    ```

2.  **Instalar dependencias:**
    ```bash
    pip install -r requirements.txt
    playwright install chromium
    ```

3.  **Configurar entorno:**
    *   Crea un archivo `.env` basado en `.env.example`.
    *   Agrega tu `GOOGLE_API_KEY` (para TMO/Crawler) y `DISCORD_TOKEN` (si usarás el bot).

## 📖 Cómo Usar

### 🖥️ Opción A: App de Escritorio
Ejecuta la interfaz gráfica para uso personal.
```powershell
python app.py
```
1.  Pega el enlace en el campo de texto.
2.  Presiona "Descargar PDF".
3.  El archivo se abrirá automáticamente al terminar.

### 🤖 Opción B: Bot de Discord
Si tienes el token configurado, inicia el bot:
```powershell
python bot.py
```
*   **Comando:** `!descargar <url>`
*   Si el archivo pesa <8MB, lo sube al chat.
*   Si pesa más, lo sube automáticamente a **GoFile** y te da el link.

## 📂 Estructura del Proyecto
*   `core.py`: Lógica principal de descarga y procesamiento (Brain 🧠).
*   `app.py`: Interfaz gráfica (Tkinter).
*   `bot.py`: Cliente de Discord.
*   `PDF/`: Carpeta donde se guardan los archivos finales.

---
*Desarrollado con ayuda de Gemini* 🤖✨
