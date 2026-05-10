import asyncio
from core.handler import process_entry

async def main():
    url = "https://hitomi.la/doujinshi/wild-chronicles---darkest-desire:-chapter-1--uncensored--english-3116065.html#1"
    
    def log_cb(msg):
        print(msg)
        
    def cancel_cb():
        return False
        
    def progress_cb(curr, tot):
        pass
        
    await process_entry(url, log_cb, cancel_cb, progress_cb)

if __name__ == "__main__":
    asyncio.run(main())
