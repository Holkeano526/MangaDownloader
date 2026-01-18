import asyncio
from crawl4ai import AsyncWebCrawler

async def analyze():
    url = "https://hitomi.la/doujinshi/_oharami-sama_2_-english-_--decensored--english-3693343.html"
    print(f"🔍 Analyzing: {url}")
    
    async with AsyncWebCrawler(verbose=True) as crawler:
        result = await crawler.arun(url=url, bypass_cache=True)
        if result.success:
            print("✅ Page loaded")
            with open("hitomi_gallery.html", "w", encoding="utf-8") as f:
                f.write(result.html)
            print("💾 Saved hitomi_gallery.html")
        else:
            print(f"❌ Failed: {result.error_message}")

if __name__ == "__main__":
    asyncio.run(analyze())
