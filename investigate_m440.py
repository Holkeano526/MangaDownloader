import asyncio
import os
from crawl4ai import AsyncWebCrawler
from crawl4ai.async_configs import LLMConfig
from crawl4ai.extraction_strategy import LLMExtractionStrategy

async def analyze():
    print("🔍 Analizando m440.in...")
    
    # 1. Analizar página de un capítulo específico
    manga_url = "https://m440.in/manga/kaasan-datte-onna-nandayo/1-m72lg" 
    
    async with AsyncWebCrawler(verbose=True) as crawler:
        print(f"📄 Crawling Chapter: {manga_url}")
        result = await crawler.arun(url=manga_url, bypass_cache=True)
        
        if result.success:
            print("✅ Chapter Page Loaded")
            with open("m440_chapter.html", "w", encoding="utf-8") as f:
                f.write(result.html)
            print("💾 Saved m440_chapter.html")
        else:
            print(f"❌ Failed to load chapter: {result.error_message}")

    # 3. Test Image Download
    img_url = "https://s1.m440.in/uploads/manga/kaasan-datte-onna-nandayo/chapters/1-m72lg/993_1.jpg"
    print(f"🖼 Testing download: {img_url}")
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.get(img_url, headers={"User-Agent": "Mozilla/5.0"}) as resp:
            print(f"Status: {resp.status}")
            if resp.status == 200:
                print("✅ Image downloadable with simple User-Agent")
            else:
                print("❌ Image blocked, need Referer?")
                async with session.get(img_url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://m440.in/"}) as resp2:
                    print(f"Status with Referer: {resp2.status}")

        # 2. Analizar un capítulo (si encontramos uno en el HTML, lo haré manualmente si no)
        # Voy a asumir una URL de capítulo probable o tratar de extraerla rápido
        # Pero primero quiero ver el HTML del manga para saber cómo extraer los capítulos.

if __name__ == "__main__":
    asyncio.run(analyze())
