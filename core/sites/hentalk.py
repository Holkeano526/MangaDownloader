"""
Hentalk.pw site handler.
Very similar to fakku.cc - uses /image/HASH/PAGENUMBER pattern.
Gallery data is embedded as JSON in a SvelteKit script tag.
"""
import asyncio
import os
import re
import json
import shutil
import uuid
from typing import Callable, Optional
from playwright.async_api import async_playwright

from .base import BaseSiteHandler
from .. import config
from ..utils import clean_filename

# Semaphore to limit concurrent downloads and avoid 429
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(5)


class HentalkHandler(BaseSiteHandler):
    """Handler for hentalk.pw"""

    @staticmethod
    def get_supported_domains() -> list:
        return ["hentalk.pw"]

    async def process(
        self,
        url: str,
        log_callback: Callable[[str], None],
        check_cancel: Callable[[], bool],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> None | list[str]:
        """
        Download images from hentalk.pw using Playwright to extract gallery data
        (hash, total pages, title) from the embedded JSON, then downloading images
        in parallel with a semaphore.
        """
        id_match = re.search(r"/g/(\d+)", url)
        if not id_match:
            log_callback(
                "[ERROR] Could not extract Gallery ID from URL. URL must contain /g/ID"
            )
            return

        gallery_id = id_match.group(1)
        log_callback(f"[INIT] Processing Hentalk ID: {gallery_id}...")

        # Create temp directory
        current_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        unique_id = uuid.uuid4().hex[:8]
        temp_dir = os.path.join(
            current_dir, config.TEMP_FOLDER_NAME, f"hentalk_{gallery_id}_{unique_id}"
        )
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        os.makedirs(temp_dir, exist_ok=True)

        download_targets = []
        title = f"Hentalk_{gallery_id}"
        total_pages = 0
        image_hash = ""

        async with async_playwright() as p:
            is_headless = (
                os.getenv("HEADLESS", "false").lower() == "true"
                or not os.getenv("DISPLAY")
            )
            if os.name == "nt":
                is_headless = True

            browser = await p.chromium.launch(
                headless=is_headless,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--start-maximized"],
            )
            context = await browser.new_context(
                user_agent=config.USER_AGENT, viewport={"width": 1280, "height": 720}
            )
            page = await context.new_page()

            try:
                gallery_url = f"https://hentalk.pw/g/{gallery_id}"
                log_callback(f"[INFO] Opening gallery: {gallery_url}")
                await page.goto(gallery_url, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)

                # --- Extract gallery data from embedded SvelteKit JSON ---
                # The page embeds data as: ...gallery:{id:18677,hash:"hex",title:"...",pages:22,...}...
                html_content = await page.content()

                gallery_data = self._extract_gallery_data_from_html(html_content, log_callback)

                if gallery_data:
                    image_hash = gallery_data.get("hash", "")
                    total_pages = gallery_data.get("pages", 0)
                    if gallery_data.get("title"):
                        title = gallery_data["title"]
                    log_callback(
                        f"[INFO] Extracted from JSON: hash={image_hash}, pages={total_pages}, title={title}"
                    )

                # Fallback: extract title from DOM og:title
                if not gallery_data or not gallery_data.get("title"):
                    try:
                        og_title = page.locator('meta[property="og:title"]').first
                        if await og_title.count() > 0:
                            title_raw = await og_title.get_attribute("content")
                            if title_raw:
                                # Format: "Title - faccina"
                                title = title_raw.replace(" - faccina", "").strip()
                                log_callback(f"[INFO] Title from og:title: {title}")
                    except Exception:
                        pass

                # Fallback: extract total pages from DOM counter
                if not total_pages:
                    try:
                        page_counter = page.locator("text=/\\d+\\s*/\\s*\\d+/").first
                        counter_text = await page_counter.inner_text()
                        parts = counter_text.split("/")
                        if len(parts) == 2:
                            total_pages = int(parts[1].strip())
                            log_callback(
                                f"[INFO] Total pages extracted via DOM counter: {total_pages}"
                            )
                    except Exception:
                        log_callback(
                            "[WARN] DOM page counter not found. Falling back to regex..."
                        )
                        pages_match = re.search(
                            r">\s*1\s*/\s*(\d+)\s*<", html_content
                        )
                        if pages_match:
                            total_pages = int(pages_match.group(1))
                        else:
                            log_callback(
                                "[ERROR] Could not determine total pages from DOM or regex."
                            )
                            return

                # Fallback: extract image hash from img tags
                if not image_hash:
                    try:
                        img_element = page.locator("img[src*='/image/']").first
                        img_src = await img_element.get_attribute("src")
                        if img_src:
                            hash_match = re.search(
                                r"/image/([a-fA-F0-9]+)/", img_src
                            )
                            if hash_match:
                                image_hash = hash_match.group(1)
                                log_callback(
                                    f"[INFO] Image hash extracted via DOM: {image_hash}"
                                )
                    except Exception:
                        pass

                if not image_hash:
                    # Last resort: regex on raw HTML
                    hash_match = re.search(r'hash:"([a-fA-F0-9]+)"', html_content)
                    if hash_match:
                        image_hash = hash_match.group(1)
                        log_callback(
                            f"[INFO] Image hash extracted via regex: {image_hash}"
                        )
                    else:
                        log_callback(
                            "[ERROR] Could not extract image hash. Page format changed?"
                        )
                        return

                log_callback(
                    f"[INFO] Ready to download {total_pages} pages with hash {image_hash}"
                )

                # --- Parallel download with Semaphore ---
                completed_count = [0]

                async def download_page(i: int) -> Optional[str]:
                    async with DOWNLOAD_SEMAPHORE:
                        if check_cancel():
                            return None
                        try:
                            # Image URL: /image/HASH/PAGENUMBER (no query params = original)
                            img_url = f"https://hentalk.pw/image/{image_hash}/{i}"
                            response = await page.request.get(
                                img_url, headers={"Referer": gallery_url}
                            )

                            if response.status == 200:
                                data = await response.body()
                                ext = "png"  # hentalk serves PNG images by default
                                ct = response.headers.get("content-type", "")
                                if "jpeg" in ct or "jpg" in ct:
                                    ext = "jpg"
                                elif "webp" in ct:
                                    ext = "webp"
                                elif "gif" in ct:
                                    ext = "gif"

                                filename = f"{i:03d}.{ext}"
                                filepath = os.path.join(temp_dir, filename)

                                def _write_file():
                                    with open(filepath, "wb") as f:
                                        f.write(data)

                                await asyncio.to_thread(_write_file)

                                completed_count[0] += 1
                                if progress_callback:
                                    progress_callback(
                                        completed_count[0], total_pages
                                    )
                                return filepath
                            else:
                                log_callback(
                                    f"[ERROR] Page {i} failed with status {response.status}"
                                )
                                return None
                        except Exception as e:
                            log_callback(f"[ERROR] Error downloading page {i}: {e}")
                            return None

                tasks = [download_page(i) for i in range(1, total_pages + 1)]
                results = await asyncio.gather(*tasks)
                download_targets = [r for r in results if r is not None]

                log_callback(
                    f"\n[INFO] Download finished. {len(download_targets)} images retrieved."
                )

            except Exception as e:
                log_callback(f"[ERROR] Playwright global error: {e}")
            finally:
                await browser.close()

        # Generate PDF via shared utils
        if download_targets:
            from ..utils import async_finalize_pdf_flow

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

    @staticmethod
    def _extract_gallery_data_from_html(
        html: str, log_callback: Callable[[str], None]
    ) -> Optional[dict]:
        """
        Extracts gallery data from the embedded SvelteKit JSON in the page source.
        The data looks like: gallery:{id:18677,hash:"hex",title:"...",pages:22,...}

        Returns a dict with keys: hash, pages, title, or None if not found.
        """
        try:
            # Find the gallery data block
            gallery_match = re.search(
                r'gallery:\{id:\d+,hash:"([a-fA-F0-9]+)",title:"([^"]*)",.*?pages:(\d+)',
                html,
                re.DOTALL,
            )
            if gallery_match:
                return {
                    "hash": gallery_match.group(1),
                    "title": gallery_match.group(2),
                    "pages": int(gallery_match.group(3)),
                }

            # Alternative: try parsing from the full data block
            # Search for: data:{gallery:{...
            data_match = re.search(
                r'data:\{gallery:\{id:\d+,hash:"([^"]+)",title:"([^"]*)",[^}]*pages:(\d+)',
                html,
                re.DOTALL,
            )
            if data_match:
                return {
                    "hash": data_match.group(1),
                    "title": data_match.group(2),
                    "pages": int(data_match.group(3)),
                }

            log_callback(
                "[WARN] Could not parse gallery data from embedded JSON. Will use DOM fallbacks."
            )
        except Exception as e:
            log_callback(f"[WARN] Error parsing embedded JSON: {e}")
        return None