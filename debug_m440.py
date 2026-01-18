import asyncio
import os
from crawl4ai import AsyncWebCrawler

async def main():
    url = "https://m440.in/manga/isekai-anthology-isekai-demon-demon-mother-and-daughter"
    print(f"Fetching {url}...")
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(url=url, bypass_cache=True)
        if result.success:
            print("Fetch successful.")
            with open("m440_debug.html", "w", encoding="utf-8") as f:
                f.write(result.html)
            print("Saved to m440_debug.html")
        else:
            print(f"Fetch failed: {result.error_message}")

if __name__ == "__main__":
    asyncio.run(main())
