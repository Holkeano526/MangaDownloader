import requests

headers = {
    "Referer": "https://hitomi.la/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

url = "https://ltn.gold-usergeneratedcontent.net/galleries/3705927.js"

try:
    print(f"Fetching {url}...")
    resp = requests.get(url, headers=headers)
    print(f"Status Code: {resp.status_code}")
    print("--- Content Start ---")
    print(resp.text[:500])
    print("--- Content End ---")
    
    with open("hitomi_debug.js", "w", encoding="utf-8") as f:
        f.write(resp.text)
        
except Exception as e:
    print(f"Error: {e}")
