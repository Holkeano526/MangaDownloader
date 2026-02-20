
"""
TMOHentai site handler.
"""
import os
import re
import json
from typing import Callable, Optional
from crawl4ai import AsyncWebCrawler
from crawl4ai.async_configs import LLMConfig
from crawl4ai.extraction_strategy import LLMExtractionStrategy

from .base import BaseSiteHandler
from .. import config
from ..utils import download_and_make_pdf, clean_filename


class TMOHandler(BaseSiteHandler):
    """Handler for TMOHentai website."""
    
    @staticmethod
    def get_supported_domains() -> list:
        return ["tmohentai"]
    
    async def process(
        self,
        url: str,
        log_callback: Callable[[str], None],
        check_cancel: Callable[[], bool],
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> None:
        """Process TMOHentai URL using Gemini AI for extraction."""
        log_callback("[INIT] Processing TMO...")
        
        # Set API key environment variable for crawl4ai if needed or passed directly
        if config.GOOGLE_API_KEY:
             os.environ["GOOGLE_API_KEY"] = config.GOOGLE_API_KEY
        
        # Adjust URL for cascading view
        target_url = url
        if "/contents/" in url:
            target_url = url.replace("/contents/", "/reader/") + "/cascade"
        elif "/paginated/" in url:
            target_url = re.sub(r'/paginated/\d+', '/cascade', url)
        
        # Configure AI Extraction
        # Note: We rely on GOOGLE_API_KEY being set in env or config
        if not config.GOOGLE_API_KEY:
            log_callback("[WARN] GOOGLE_API_KEY is missing. AI extraction might fail.")

        llm_config = LLMConfig(provider="gemini/gemini-1.5-flash", api_token=config.GOOGLE_API_KEY)
        instruction = "Extract all image URLs. Look for 'data-original' and 'src'. Prioritize 'data-original'. Return JSON {'images': ['url1'...]}."
        llm_strategy = LLMExtractionStrategy(llm_config=llm_config, instruction=instruction)

        # JS to lazy-load images
        js_lazy_load = """
        (async () => {
            const sleep = (ms) => new Promise(r => setTimeout(r, ms));
            let totalHeight = 0; let distance = 500;
            while(totalHeight < document.body.scrollHeight) { window.scrollBy(0, distance); totalHeight += distance; await sleep(100); }
            window.scrollTo(0, 0);
            document.querySelectorAll('img[data-original]').forEach(img => { img.src = img.getAttribute('data-original'); });
            await sleep(1000);
        })();
        """

        async with AsyncWebCrawler(verbose=True) as crawler:
            result = await crawler.arun(
                target_url,
                extraction_strategy=llm_strategy,
                bypass_cache=True,
                js_code=js_lazy_load,
                wait_for="css:img.content-image"
            )
            
            if result.success:
                image_urls = []
                # Parse AI Result
                try:
                    if result.extracted_content:
                        clean = result.extracted_content
                        if "```json" in clean: 
                            clean = clean.split("```json")[1].split("```")[0].strip()
                        elif "```" in clean: 
                            clean = clean.split("```")[1].split("```")[0].strip()
                        image_urls = json.loads(clean).get("images", [])
                except Exception as e:
                    log_callback(f"[WARN] Error parsing AI response: {e}")
                
                # Fallback Regex
                if not image_urls and result.html:
                    matches = re.findall(r'data-original=["\'](https://[^"\']+\.(?:webp|jpg|png))["\']', result.html)
                    if matches: 
                        image_urls = sorted(list(set(matches)))

                image_urls = [u for u in image_urls if "blank.gif" not in u]

                if image_urls:
                    log_callback(f"[INFO] Images found: {len(image_urls)}")
                    pdf_name = "manga_tmo.pdf"
                    if result.html:
                        match = re.search(r'<h1[^>]*class=["\'].*?reader-title.*?["\'][^>]*>(.*?)</h1>', result.html, re.IGNORECASE | re.DOTALL)
                        if match:
                            safe = clean_filename(match.group(1).strip()).replace("\n", " ")
                            if safe: 
                                pdf_name = f"{safe}.pdf"
                    
                    await download_and_make_pdf(
                        image_urls,
                        pdf_name,
                        config.HEADERS_TMO,
                        log_callback,
                        check_cancel,
                        progress_callback=progress_callback,
                        open_result=config.OPEN_RESULT_ON_FINISH
                    )
                else:
                    log_callback("[ERROR] No images found via AI or Regex.")
            else:
                log_callback(f"[ERROR] Crawler failed: {result.error_message}")
