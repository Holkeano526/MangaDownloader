# Manga Downloader

Herramienta en Python para descargar mangas y doujinshis desde múltiples fuentes, convirtiéndolos automáticamente a PDF. Diseñada para gestionar protecciones anti-bot (Cloudflare, 403) y facilitar la lectura offline.

## Características

- **Soporte Multi-sitio**: Descarga desde TMO, ZonaTMO, M4\*\*, H2R, Hi\*\*\*\* y nh\*\*\*\*\*.
- **Conversión Automática**: Genera un único archivo PDF por capítulo o galería.
- **Modos de Ejecución**:
  - **GUI Local**: Interfaz gráfica simple (Tkinter) para escritorio.
  - **Bot de Discord**: Ejecución remota con subida automática a Discord o GoFile para archivos grandes.
- **Evasión de Protecciones**: Uso de Playwright y headers personalizados para sitios protegidos.
- **Docker Ready**: Listo para desplegar en contenedores.

## Sitios Soportados

| Sitio | Método | Notas |
|-------|--------|-------|
| **ZonaTMO / TMO** | Crawler | Series completas y capítulos individuales |
| **M4\*\*.in** | Crawler | Portadas y capítulos |
| **H2R** | JSON API | Extracción directa |
| **Hi\*\*\*\*.la** | Playwright | Imágenes Full Resolution |
| **nh\*\*\*\*\*.net** | Playwright | Bypass Cloudflare |

## Instalación

1.  **Clonar repositorio**
    ```bash
    git clone https://github.com/Holkeano526/MangaDownloader.git
    cd manga-downloader
    ```

2.  **Instalar dependencias**
    ```bash
    pip install -r requirements.txt
    playwright install chromium
    ```

3.  **Configuración**
    Renombra `.env.example` a `.env` y configura tus credenciales (necesario para el Bot de Discord o TMO).

## Uso

### Interfaz de Escritorio
```bash
python app.py
```

### Bot de Discord
```bash
python bot.py
```
*Comandos*: `!descargar <url>`

### Docker
Descargar e iniciar el contenedor en segundo plano:
```bash
docker build -t manga-downloader .
docker run -d --env-file .env --name manga-bot manga-downloader
```

## Estructura
- `core.py`: Lógica de descarga y procesamiento.
- `app.py`: GUI (Tkinter).
- `bot.py`: Cliente de Discord.
- `PDF/`: Directorio de salida.
