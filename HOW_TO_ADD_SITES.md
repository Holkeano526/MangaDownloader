# Guía para Añadir Nuevos Módulos (Sitios Web) a MangaDownloader

Esta guía está diseñada para Programadores Senior y Agentes de IA encargados de expandir el soporte de MangaDownloader a nuevas páginas web. La aplicación utiliza un **Patrón Strategy**, lo que significa que cada sitio web tiene su propia clase aislada y estandarizada, haciendo que el proyecto sea altamente modular.

---

## 1. Archivos Necesarios a Modificar

Para agregar soporte a una nueva web (por ejemplo, `nuevomanga.com`), solo necesitas interactuar con dos lugares del proyecto:

1. **Crear el módulo del sitio:** 
   Debes crear un nuevo archivo de Python dentro de la carpeta `core/sites/` (ej. `core/sites/nuevomanga.py`).
2. **Registrar el módulo:** 
   Debes importar y añadir la nueva clase a la lista `HANDLERS` en el archivo `core/handler.py`.

No es necesario tocar `app.py`, `bot.py` ni `web_server.py`. Toda la interfaz funcionará automáticamente en cascada.

---

## 2. Estructura del Nuevo Módulo

Todo nuevo módulo debe heredar de `BaseSiteHandler` (ubicado en `core/sites/base.py`). 
Aquí tienes la plantilla base:

```python
import asyncio
import os
from typing import Callable, Optional
from playwright.async_api import async_playwright
from .base import BaseSiteHandler
from ..utils import clean_filename, async_finalize_pdf_flow

class NuevoMangaHandler(BaseSiteHandler):
    """Handler para nuevomanga.com"""
    
    @staticmethod
    def get_supported_domains() -> list:
        # Devuelve los dominios exactos que activarán este scraper
        return ["nuevomanga.com"]
    
    async def process(
        self,
        url: str,
        log_callback: Callable[[str], None],
        check_cancel: Callable[[], bool],
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> None | list[str]:
        
        log_callback("[INFO] Iniciando descarga desde NuevoManga...")
        # Lógica de scraping y descarga aquí...
```

---

## 3. Flujo de Trabajo para el Agente IA: Cómo Analizar la Web

Cuando a un agente de IA se le pide que añada una nueva web, **no debe adivinar el código**. Debe seguir este proceso de investigación:

### Paso A: Análisis Inicial de la Web
1. Descarga el HTML de un capítulo de manga de la web objetivo (usando herramientas como curl, playwright en consola o inspeccionando el DOM).
2. Identifica si el sitio es un SSR (Server-Side Rendering) tradicional donde las imágenes están directamente en el código fuente, o una SPA (Single Page Application) protegida por Cloudflare donde el contenido se carga dinámicamente mediante JavaScript.

### Paso B: Uso del DOM vs Expresiones Regulares
**Regla de Oro:** ¡Prohibido usar expresiones regulares frágiles sobre el código fuente crudo para extraer elementos clave! La web cambiará su diseño y la expresión regular fallará silenciosamente.

Debes utilizar el DOM (a través de **Playwright**) con selectores CSS o XPath precisos.
Busca elementos robustos:
- ID's únicos: `page.locator("#manga-title")`
- Atributos semánticos: `page.locator('meta[property="og:title"]')`
- Selectores jerárquicos fiables: `page.locator(".reader-images-container img")`

### Paso C: Lógica de Extracción Recomendada (Playwright)

Si el sitio requiere evadir protecciones antibot, el estándar de este proyecto es usar `async_playwright`:

```python
async with async_playwright() as p:
    browser = await p.chromium.launch(headless=True) # Usar configuración global headless si aplica
    context = await browser.new_context(user_agent="Tu_User_Agent_Configurado")
    page = await context.new_page()
    
    await page.goto(url, wait_until="domcontentloaded")
    
    # 1. Extraer el Título
    titulo = await page.locator("h1.chapter-title").inner_text()
    
    # 2. Localizar todas las imágenes del capítulo
    imagenes_locators = await page.locator("img.manga-page").all()
    image_urls = []
    
    for img in imagenes_locators:
        src = await img.get_attribute("src")
        # o 'data-src' si el sitio usa lazy-loading
        if src: image_urls.append(src)
        
    await browser.close()
```

### Paso D: Descarga Paralela y Generación de PDF

Una vez extraídos los enlaces de las imágenes (URLs crudas), **no descargues las imágenes de manera sincrónica**.
- Usa `asyncio.Semaphore(5)` (o un número prudente para no provocar error HTTP 429) y lanza peticiones concurrentes para descargar las imágenes.
- Finalmente, utiliza el módulo compartido de la aplicación para ensamblar las fotos en el PDF de manera segura y sin bloquear el servidor web.

Ejemplo del cierre del proceso:
```python
from ..utils import async_finalize_pdf_flow

pdf_name = f"{clean_filename(titulo)}.pdf"
await async_finalize_pdf_flow(
    download_targets,  # Lista de rutas absolutas de imágenes descargadas (.jpg, .png)
    pdf_name, 
    log_callback, 
    temp_dir,          # Carpeta temporal a limpiar tras la conversión
    open_result=True
)
```

---

## 4. Retornos Especiales (Manejo de Series)

Si el usuario inserta la URL de la página principal del manga (la "Serie") en lugar de un capítulo, tu método `process` puede retornar una lista de strings (`list[str]`), donde cada string es el enlace a un capítulo individual.
El `handler.py` y las diferentes interfaces de usuario (Bot, Web, Desktop) detectarán esta lista automáticamente y pondrán todos los capítulos en cola para su descarga secuencial.

```python
if es_url_de_serie(url):
    enlaces_capitulos = await extraer_lista_de_capitulos(page)
    return enlaces_capitulos # Retorna list[str]
```
