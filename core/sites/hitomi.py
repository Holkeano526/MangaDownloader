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

from .base import BaseSiteHandler
from .. import config
from ..utils import async_finalize_pdf_flow, clean_filename

# Semaphore for parallel downloads (2 concurrent to avoid bans)
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(2)


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
                await page.wait_for_timeout(3000)

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

                # Step 4: Parallel downloads with semaphore and retries
                completed_count = [0]

                async def download_page(idx: int, img_url: str) -> Optional[str]:
                    async with DOWNLOAD_SEMAPHORE:
                        if check_cancel():
                            return None

                        max_retries = 3
                        for attempt in range(max_retries):
                            try:
                                headers = {
                                    "Referer": f"https://hitomi.la/reader/{gallery_id}.html"
                                }
                                response = await page.request.get(
                                    img_url, headers=headers
                                )
                                if response.status == 200:
                                    data = await response.body()
                                    ext = img_url.split(".")[-1].split("?")[0]
                                    if ext not in ("jpg", "jpeg", "png", "webp", "gif", "avif"):
                                        ext = "webp"
                                    filepath = os.path.join(temp_dir, f"{idx:03d}.{ext}")

                                    def _write_file():
                                        with open(filepath, "wb") as f:
                                            f.write(data)

                                    await asyncio.to_thread(_write_file)

                                    completed_count[0] += 1
                                    if progress_callback:
                                        progress_callback(
                                            completed_count[0], total_images
                                        )
                                    return filepath
                                elif response.status in (503, 429, 403):
                                    log_callback(
                                        f"[WARN] Page {idx} got {response.status}. Retrying {attempt+1}/{max_retries}..."
                                    )
                                    await asyncio.sleep(4 * (attempt + 1))  # Exponential backoff
                                else:
                                    log_callback(
                                        f"[ERROR] Page {idx} failed with status {response.status}"
                                    )
                                    return None
                            except Exception as e:
                                log_callback(
                                    f"[WARN] Error downloading page {idx}: {e}. Retrying {attempt+1}/{max_retries}..."
                                )
                                await asyncio.sleep(4 * (attempt + 1))

                        log_callback(f"[ERROR] Page {idx} failed permanently after {max_retries} retries.")
                        return None

                # Launch all download tasks concurrently (controlled by semaphore)
                tasks = [
                    download_page(i + 1, url) for i, url in enumerate(image_urls)
                ]
                results = await asyncio.gather(*tasks)
                download_targets = [r for r in results if r is not None]

                log_callback(
                    f"\n[INFO] Download finished. {len(download_targets)} images retrieved."
                )

            except Exception as e:
                log_callback(f"[ERROR] Playwright global error: {e}")
            finally:
                await browser.close()

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