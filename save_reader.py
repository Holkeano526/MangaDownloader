import requests

headers = {
    "Referer": "https://hitomi.la/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

url = "https://hitomi.la/reader/3705927.html"

try:
    resp = requests.get(url, headers=headers)
    with open("hitomi_reader.html", "w", encoding="utf-8") as f:
        f.write(resp.text)
    print("Saved hitomi_reader.html")
    
except Exception as e:
    print(f"Error: {e}")
