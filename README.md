# Universal Manga PDF Downloader

A powerful tool to automate manga downloads from popular sites and convert them into high-quality PDFs. Now featuring a robust **Web Interface** and **Modular Core**.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.95%2B-009688)
![React](https://img.shields.io/badge/React-Vite-61DAFB)
![Playwright](https://img.shields.io/badge/Playwright-Automation-orange)

##  New Features & Technologies

###  Architecture
-   **Modular Core**: Completely refactored `core` package using the Strategy Pattern for easy extension.
-   **Asynchronous I/O**: Built on `asyncio` and `aiohttp` for high-performance concurrent downloads.
-   **Smart Extraction**: Utilizes **Crawl4AI** and **Google Gemini 1.5 Flash** for intelligent image extraction and parsing of complex sites.

###  Web Interface
-   **Modern UI**: Built with **React** and **Vite**.
-   **Real-time Feedback**: WebSocket integration for live logs and progress bars.
-   **Dual Server**: Managed by `START_WEB_VERSION.bat` which launches both Backend (FastAPI) and Frontend.

###  enhanced Automation
-   **Playwright Integration**: Bypasses Cloudflare and 403 Forbidden errors on strict sites (Hi.la, NH).
-   **Browser Simulation**: Mimics real user behavior for "Stealth Mode" scraping.

---

##  Supported Sites

| Site | Method | Technology | Notes |
|------|--------|------------|-------|
| **Z-TMO** | Crawler + Cascade | **Crawl4AI + Gemini** | Supports full series and single chapters. |
| **TMO-H** | AI Extraction | **Crawl4AI + Gemini** | Intelligent image detection. |
| **M440** | Crawler | **Crawl4AI** | Supports covers and chapters. |
| **H2R**| JSON Parsing | **AsyncIO** | Fast metadata extraction. |
| **Hi.la** | Stealth Browser | **Playwright** | Bypasses 404/403 protection. |
| **NH.net**| API + Browser | **Playwright** | Bypasses Cloudflare. |

---

## 📦 Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/Holkeano526/MangaDownloader.git
    cd MangaDownloader
    ```

2.  **Environment Setup:**
    Create a `.env` file in the root directory:
    ```ini
    GOOGLE_API_KEY=your_gemini_api_key
    DISCORD_TOKEN=your_discord_token  # Optional
    HEADLESS=true                     # Optional (Default: false)
    ```

3.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    playwright install chromium
    ```

---

## 💻 Usage

### Option A: Web Version (Recommended)
Launch the full full-stack application (Backend + Frontend):
1.  Double-click `START_WEB_VERSION.bat`.
2.  The browser will open automatically at `http://localhost:5173`.
3.  Paste a link and watch the magic happen!

### Option B: Desktop App (Legacy GUI)
Run the standalone Tkinter interface:
```bash
python app.py
```

### Option C: Discord Bot
Run the Discord bot for remote downloading:
```bash
python bot.py
```
*   **Command:** `!descargar <url>`
*   Files >8MB are automatically uploaded to **GoFile**.

---

## 📂 Project Structure

```
MangaDownloader/
├── core/                   # Refactored Core Package
│   ├── sites/              # Site Handlers (Strategy Pattern)
│   ├── config.py           # Configuration
│   ├── handler.py          # Routing Logic
│   └── utils.py            # PDF & Download Utils
├── web_client/             # React Frontend
├── app.py                  # Legacy Tkinter GUI
├── bot.py                  # Discord Bot
├── web_server.py           # FastAPI Backend
└── PDF/                    # Output Directory
```

## 🐳 Docker

Run the entire stack in containers:
```bash
docker-compose up --build
```
*   Backend: `http://localhost:8000`
*   Frontend: `http://localhost:8080`
