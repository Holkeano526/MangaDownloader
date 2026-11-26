"""
Hitomi.la site handler (Batch Extraction Mode).
Uses Playwright to inject JS and extract all image URLs at once,
then downloads them in parallel with a semaphore.
"""
import asyncio
import os
import re
import shutil
import uuid
from typing import Callable, Optional
from playwright.async_api import async_playwright
import aiohttp

from .base import BaseSiteHandler
from . import hitomi_http
from .. import config
from ..utils import async_finalize_pdf_flow, clean_filename, descargar_imagenes

# Las imágenes se descargan con aiohttp y un límite POR galería (config.CONCURRENCIA_IMAGENES).
# Antes: semáforo GLOBAL de 2 compartido por todas las descargas del bot y peticiones a
# través del navegador (page.request), con el navegador abierto hasta el final.


class HitomiHandler(BaseSiteHandler):
    """Handler for Hitomi.la website."""

    @staticmethod
    def get_supported_domains() -> list:
        return ["hitomi.la"]

    async def process(
        self,
        url: str,
        log_callback: Callable[[str], None],
        check_cancel: Callable[[], bool],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> None | list[str]:
        """
        Download images from Hitomi.la using Playwright to inject JavaScript
        and extract all image URLs in a single batch, then download in parallel.
        Falls back to page-by-page navigation if batch extraction fails.
        """
        id_match = re.search(r"[-/](\d+)\.html", url)
        if not id_match:
            log_callback("[ERROR] Could not extract ID from URL.")
            return
        gallery_id = int(id_match.group(1))

        log_callback(f"[INIT] Processing Hitomi ID: {gallery_id} (Batch Mode)...")

        # Create temp directory
        current_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        unique_id = uuid.uuid4().hex[:8]
        temp_dir = os.path.join(
            current_dir, config.TEMP_FOLDER_NAME, f"hitomi_{gallery_id}_{unique_id}"
        )
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        os.makedirs(temp_dir, exist_ok=True)

        download_targets = []
        image_urls = []
        title = f"Hitomi_{gallery_id}"

        # Step 1-3 (rápido): resolver la galería por HTTP, sin navegador (~0,4 s)
        try:
            timeout = aiohttp.ClientTimeout(total=30, connect=10)
            async with aiohttp.ClientSession(headers={"User-Agent": config.USER_AGENT}, timeout=timeout) as s:
                page_title, image_urls = await hitomi_http.resolver(gallery_id, s)
            clean_title = re.sub(r'[\\/*?:"<>|]', "", page_title).strip()
            title = clean_title or title
            log_callback(f"[INFO] Title detected: {title}")
            log_callback(f"[INFO] Resolved without browser: {len(image_urls)} images.")
        except Exception as e:
            image_urls = []
            log_callback(f"[WARN] HTTP resolution failed ({e}). Falling back to browser...")

        # Respaldo: el método anterior con Playwright (ejecuta el JavaScript de Hitomi)
        if not image_urls:
            async with async_playwright() as p:
                # Headless mode: respect env vars for Docker/Discord/Linux compatibility
                is_headless = (
                    os.getenv("HEADLESS", "false").lower() == "true"
                    or not os.getenv("DISPLAY")
                )

                browser = await p.chromium.launch(
                    headless=is_headless,
                    args=["--no-sandbox", "--disable-setuid-sandbox"],
                )
                context = await browser.new_context(
                    user_agent=config.USER_AGENT, viewport={"width": 1280, "height": 720}
                )
                page = await context.new_page()

                try:
                    # Navigate to Reader
                    reader_url = f"https://hitomi.la/reader/{gallery_id}.html#1"
                    log_callback(f"[INFO] Opening reader: {reader_url}")
                    await page.goto(reader_url, wait_until="domcontentloaded")

                    try:
                        await page.wait_for_function(
                            "() => typeof window.galleryinfo !== 'undefined'", timeout=15000
                        )
                    except Exception:
                        pass

                    # Extract title
                    title = f"Hitomi_{gallery_id}"
                    page_title = await page.title()
                    if page_title:
                        clean_title = re.sub(r'[\\/*?:"<>|]', "", page_title).strip()
                        title = clean_title if clean_title else title
                    log_callback(f"[INFO] Title detected: {title}")

                    # Step 1: Wait for Hitomi's galleryinfo to initialize
                    try:
                        await page.wait_for_function(
                            "() => typeof window.galleryinfo !== 'undefined'", timeout=10000
                        )
                    except Exception:
                        log_callback(
                            "[WARN] window.galleryinfo not detected. Will attempt fallback..."
                        )

                    total_images = await page.evaluate(
                        "() => window.galleryinfo ? window.galleryinfo.files.length : 0"
                    )

                    if total_images == 0:
                        log_callback(
                            "[WARN] Could not determine total images. Will use fallback..."
                        )
                        total_images = 9999

                    log_callback(f"[INFO] Images detected: {total_images}")

                    # Step 2: Batch extraction via injected JS
                    # Hitomi distribuye imágenes en varios servidores (w1, w2, w3...).
                    # Al NO pasar el parámetro "a", url_from_url_from_hash asigna automáticamente
                    # el servidor correcto con balanceo de carga nativo.
                    image_urls = []
                    try:
                        image_urls = await page.evaluate(
                            """() => {
                            if (typeof url_from_url_from_hash === "function" && window.galleryinfo) {
                                return window.galleryinfo.files.map(f => {
                                    return url_from_url_from_hash(window.galleryid, f, "webp");
                                });
                            }
                            return [];
                        }"""
                        )
                        if image_urls and len(image_urls) > 0 and image_urls[0]:
                            log_callback(
                                f"[INFO] Batch extraction successful: {len(image_urls)} URLs extracted via JS."
                            )
                        else:
                            log_callback(
                                "[WARN] Batch JS extraction returned empty. Falling back to page-by-page..."
                            )
                    except Exception as e:
                        log_callback(
                            f"[WARN] Batch JS extraction failed: {e}. Falling back to page-by-page..."
                        )

                    # Step 3: Fallback — page-by-page extraction if batch failed
                    if not image_urls:
                        log_callback("[INFO] Starting page-by-page extraction fallback...")
                        image_urls = []
                        for i in range(1, min(total_images + 1, 10000)):
                            if check_cancel():
                                log_callback("[WARN] Process cancelled by user.")
                                break
                            try:
                                await page.evaluate(f"location.hash = '#{i}'")
                                img_src = await page.wait_for_function(
                                    """() => {
                                        const img = document.querySelector("div#comicImages img");
                                        return img && img.src && img.src.startsWith("http") ? img.src : null;
                                    }""",
                                    timeout=8000,
                                )
                                img_src = await img_src.json_value()
                                if img_src:
                                    image_urls.append(img_src)
                                    if progress_callback:
                                        progress_callback(i, total_images)
                                else:
                                    if i > 5:
                                        log_callback(
                                            "[INFO] Empty src detected. Likely end of gallery."
                                        )
                                        break
                                await page.wait_for_timeout(100)  # minimal delay
                            except Exception as e:
                                log_callback(f"[WARN] Page {i} extraction failed: {e}")
                                if total_images == 9999 and i > 5:
                                    break

                        total_images = len(image_urls)
                        log_callback(
                            f"[INFO] Page-by-page extracted {total_images} image URLs."
                        )

                    # (las imágenes se descargan después de cerrar el navegador)

                except Exception as e:
                    log_callback(f"[ERROR] Playwright global error: {e}")
                finally:
                    await browser.close()

        # Step 4: descarga paralela con aiohttp, con el navegador ya cerrado (libera RAM)
        if image_urls:
            log_callback(f"[INFO] Images detected: {len(image_urls)}")
            headers = {"Referer": f"https://hitomi.la/reader/{gallery_id}.html", "User-Agent": config.USER_AGENT}
            download_targets = await descargar_imagenes(
                image_urls, temp_dir, headers, log_callback, check_cancel, progress_callback
            )
            if check_cancel():
                # Cancelado: no se genera un PDF a medias
                log_callback("[INFO] Process cancelled by user.")
                shutil.rmtree(temp_dir, ignore_errors=True)
                return
            log_callback(f"\n[INFO] Download finished. {len(download_targets)} images retrieved.")
            if len(download_targets) < len(image_urls):
                log_callback(f"[WARN] {len(image_urls) - len(download_targets)} de {len(image_urls)} páginas no se pudieron descargar.")

        # Generate PDF via async utils (non-blocking)
        if download_targets:
            pdf_name = f"{clean_filename(title)}.pdf"
            await async_finalize_pdf_flow(
                download_targets,
                pdf_name,
                log_callback,
                temp_dir,
                open_result=config.OPEN_RESULT_ON_FINISH,
            )
        else:
            log_callback("[ERROR] No images downloaded for PDF.")