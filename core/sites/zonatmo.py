
"""
ZonaTMO site handler.
"""
import os
import re
import json
import asyncio
import aiohttp
from typing import Callable, Optional
from crawl4ai import AsyncWebCrawler
from crawl4ai.async_configs import LLMConfig
from crawl4ai.extraction_strategy import LLMExtractionStrategy

from .base import BaseSiteHandler
from .. import config
from ..utils import download_and_make_pdf, clean_filename


class ZonaTMOHandler(BaseSiteHandler):
    """Handler for ZonaTMO website."""
    
    @staticmethod
    def get_supported_domains() -> list:
        return ["zonatmo.com"]
    
    async def process(
        self,
        url: str,
        log_callback: Callable[[str], None],
        check_cancel: Callable[[], bool],
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> None:
        """Process ZonaTMO URL."""
        log_callback("[INIT] Processing ZonaTMO...")
        
        # Set API key for crawl4ai
        if config.GOOGLE_API_KEY:
             os.environ["GOOGLE_API_KEY"] = config.GOOGLE_API_KEY

        if "/library/manga/" in url:
            log_callback("[INFO] Cover detected. Searching for chapters...")
            async with AsyncWebCrawler(verbose=True) as crawler:
                result = await crawler.arun(url=url, bypass_cache=True)
                if not result.success:
                    log_callback(f"[ERROR] Failed to load cover: {result.error_message}")
                    return
                
                links = re.findall(r'href=["\'](https://zonatmo.com/view_uploads/[^"\']+)["\']', result.html)
                
                clean_links = []
                seen = set()
                for l in links:
                    if l not in seen:
                        clean_links.append(l)
                        seen.add(l)
                
                if not clean_links:
                    log_callback("[ERROR] No chapters found.")
                    return
    
                log_callback(f"[INFO] Found {len(clean_links)} chapters.")
                
                manga_title = "Manga_ZonaTMO"
                
                h1_match = re.search(r'<h1[^>]*class=["\'].*?element-title.*?["\'][^>]*>(.*?)</h1>', result.html, re.IGNORECASE | re.DOTALL)
                
                if h1_match: 
                    raw_html = h1_match.group(1)
                    raw_html = re.sub(r'<small[^>]*>.*?</small>', '', raw_html, flags=re.IGNORECASE | re.DOTALL)
                    raw_html = re.sub(r'\s+', ' ', raw_html)
                    candidate = clean_filename(raw_html) 
                    if candidate and candidate != "untitled":
                         manga_title = candidate
                
                if manga_title == "Manga_ZonaTMO":
                    title_tag = re.search(r'<title>(.*?)</title>', result.html, re.IGNORECASE)
                    if title_tag:
                        raw = title_tag.group(1).split('|')[0].split('-')[0].strip()
                        manga_title = clean_filename(raw)
    
                if not manga_title or len(manga_title) < 2: manga_title = "Manga_ZonaTMO"
                
                log_callback(f"[INFO] Title detected: {manga_title}")
                
               # Determine output directory
                pdf_dir = os.path.join(os.getcwd(), config.PDF_FOLDER_NAME, manga_title)
                os.makedirs(pdf_dir, exist_ok=True)
                
                clean_links.reverse()
    
                for i, chap_url in enumerate(clean_links):
                    if check_cancel and check_cancel(): break
                    if progress_callback: progress_callback(i + 1, len(clean_links))
                    
                    pdf_name = f"{manga_title} - {i+1:03d}.pdf"
                    full_pdf_path = os.path.join(pdf_dir, pdf_name)
                    
                    if os.path.exists(full_pdf_path): 
                        continue
                    
                    log_callback(f"Processing Cap {i+1}/{len(clean_links)}")
                    
                    try:
                        await self._process_chapter(chap_url, full_pdf_path, log_callback, check_cancel, None)
                        await asyncio.sleep(1)
                    except Exception as e:
                        log_callback(f"[ERROR] Chapter {i+1} failed: {e}")
                
                if config.OPEN_RESULT_ON_FINISH:
                    try: os.startfile(pdf_dir)
                    except: pass
    
        else:
            # Single chapter
            pdf_name = "zonatmo_chapter.pdf" # Default name if title not found
            # We don't have crawler instance here yet, process_chapter will create one or use context manager?
            # Original code used a helper process_zonatmo_chapter which initialized AsyncWebCrawler locally inside it.
            # So we can just call helper.
            await self._process_chapter(url, pdf_name, log_callback, check_cancel, progress_callback)

    async def _process_chapter(
        self, 
        url: str, 
        output_name: str, 
        log_callback: Callable[[str], None], 
        check_cancel: Callable[[], bool], 
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> None:
        target_url = url
        
        # Resolve redirects/paginated view
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=config.HEADERS_ZONATMO) as resp:
                    if resp.status == 200:
                        final_url = str(resp.url)
                        if "/paginated" in final_url:
                            target_url = final_url.replace("/paginated", "/cascade")
                        elif "/viewer/" in final_url:
                             if not final_url.endswith("/cascade"):
                                 target_url = final_url + "/cascade"
                    else:
                        log_callback(f"[WARN] URL resolution failed: {resp.status}, using original.")
        except Exception as e:
             log_callback(f"[DEBUG] Error resolving redirect: {e}")
    
        log_callback(f"[INFO] Cascade URL: {target_url}")
    
        if not config.GOOGLE_API_KEY:
            log_callback("[WARN] GOOGLE_API_KEY missing. ZonaTMO extraction might fail.")

        llm_config = LLMConfig(provider="gemini/gemini-1.5-flash", api_token=config.GOOGLE_API_KEY or "")
        instruction = "Extract all image URLs. Look for 'data-original' and 'src'. Return JSON {'images': ['url1'...]}."
        llm_strategy = LLMExtractionStrategy(llm_config=llm_config, instruction=instruction)
        
        js_lazy_load = """
        (async () => {
            const sleep = (ms) => new Promise(r => setTimeout(r, ms));
            window.scrollTo(0, 0);
            let totalHeight = 0; let distance = 1000;
            while(totalHeight < document.body.scrollHeight) { window.scrollBy(0, distance); totalHeight += distance; await sleep(200); }
            await sleep(1000);
        })();
        """
    
        async with AsyncWebCrawler(verbose=True) as crawler:
            result = await crawler.arun(
                target_url, 
                extraction_strategy=llm_strategy, 
                bypass_cache=True, 
                js_code=js_lazy_load
            ) 
            
            image_urls = []
            if result.success:
                try:
                    if result.extracted_content:
                        clean = result.extracted_content
                        if "```json" in clean: clean = clean.split("```json")[1].split("```")[0].strip()
                        elif "```" in clean: clean = clean.split("```")[1].split("```")[0].strip()
                        image_urls = json.loads(clean).get("images", [])
                except: pass
                
                if not image_urls and result.html:
                    matches = re.findall(r'(https?://(?:img1?\.?tmo\.com|otakuteca\.com|img1tmo\.com)[^"\'\s]+\.(?:webp|jpg|png))', result.html)
                    if matches: image_urls = sorted(list(set(matches)))
            
            image_urls = [u for u in image_urls if "cover" not in u and "avatar" not in u and "banner" not in u]
    
            if image_urls:
                log_callback(f"[INFO] Images found: {len(image_urls)}")
                
                final_pdf = output_name
                # If output_name is generic, try to find title
                if output_name == "zonatmo_chapter.pdf" and result.html:
                     match = re.search(r'<h1[^>]*>(.*?)</h1>', result.html)
                     if match:
                         final_pdf = f"{clean_filename(match.group(1))}.pdf"
    
                is_path = "/" in final_pdf or "\\" in final_pdf
                await download_and_make_pdf(
                    image_urls, 
                    final_pdf, 
                    config.HEADERS_ZONATMO, 
                    log_callback, 
                    check_cancel, 
                    progress_callback, 
                    is_path=is_path,
                    open_result=config.OPEN_RESULT_ON_FINISH
                )
            else:
                log_callback("[ERROR] No images found.")
