import asyncio
from crawl4ai import AsyncWebCrawler

async def analyze():
    url = "https://hentai2read.com/oharamisama_second_story/1/"
    print(f"🔍 Analyzing: {url}")
    
    async with AsyncWebCrawler(verbose=True) as crawler:
        result = await crawler.arun(url=url, bypass_cache=True)
        if result.success:
            print("✅ Page loaded")
            with open("h2r_chapter.html", "w", encoding="utf-8") as f:
                f.write(result.html)
            print("💾 Saved h2r_chapter.html")
        else:
            print(f"❌ Failed: {result.error_message}")

if __name__ == "__main__":
    asyncio.run(analyze())
