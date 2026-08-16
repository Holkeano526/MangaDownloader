# Universal Manga PDF Downloader

A powerful tool to automate manga downloads from popular sites and convert them into high-quality PDFs. Built with a robust **Next.js Web Interface** and a **Python Modular Core**.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.95%2B-009688)
![Next.js](https://img.shields.io/badge/Next.js-15-black)
![Playwright](https://img.shields.io/badge/Playwright-Automation-orange)

## ✨ New Features & Technologies

### 🚀 Performance & Stability (New)
- **High-Concurrency Downloads**: Utilizes `asyncio.Semaphore` to parallelize image downloads without triggering rate limits (429 Too Many Requests).
- **Thread-Safe UI & Background Tasks**: Resolves race conditions in the Tkinter Desktop app using Python's thread-safe `queue.Queue` and offloads CPU-bound PDF generation to `asyncio.to_thread`.
- **Memory & Cache Optimization**: Prevents memory leaks in the FastAPI server by automatically pruning old WebSocket task history, and uses O(1) caching for rapid Discord Bot file lookups.
- **Cross-Platform Readiness**: File handling and execution gracefully adapt to Windows, macOS, and Headless Linux environments (Docker).
- **Connection Pooling**: Implements reusable `aiohttp` HTTP sessions with Keep-Alive to drastically reduce connection overhead.

### ⚡ Recent Updates (Latest Release)
- **Hentalk.pw Integration**: Added `HentalkHandler` supporting extremely fast SvelteKit JSON data extraction and Playwright Cloudflare bypass for high-quality downloads.
- **Hitomi.la Batch Optimization & Stability**: Completely refactored `HitomiHandler` to extract image URLs in batch directly via Javascript injection, skipping slow page-by-page rendering. Implemented native Hitomi load-balancing support (`w1`, `w2`, etc.), intelligent retry mechanisms (Exponential Backoff), and reduced concurrent connections to prevent `503 Service Unavailable` bans and IP blocks.
- **Dynamic Domain Loading**: Improved the legacy Tkinter app (`app.py`) to dynamically fetch and register supported domains directly from `core.handler.HANDLERS`, eliminating the need to update hardcoded lists when adding new sites.
- **Extensibility Guide**: Created `HOW_TO_ADD_SITES.md` documenting best practices for AI agents and developers adding new web scrapers using Playwright and Crawl4AI.

### 🏗️ Architecture
-   **Modular Core**: Completely refactored `core` package using the Strategy Pattern for easy extension.
-   **Asynchronous I/O**: Built on `asyncio` and `aiohttp` for high-performance concurrent downloads.
-   **Smart Extraction**: Utilizes **Crawl4AI** for intelligent image extraction and parsing of complex sites.

### 🌐 Web Interface
-   **Modern Dashboard**: Built with **Next.js 15**, TailwindCSS and Sileo Notifications.
-   **Real-time Feedback**: WebSocket integration for live logs and progress bars.
-   **Automated Launcher**: Managed by `START_WEB_VERSION.bat` which launches both Backend (FastAPI) and Frontend simultaneously.

### 🛡️ Open Source Security
-   **SSRF Protection**: Strict hostname verification dynamically applied strictly to incoming scraper requests.
-   **LFI / Path Traversal Prevention**: Absolute path checking guarantees PDFs can only be downloaded from strictly defined folders.
-   **Strict CORS Enforcement**: Mitigates Cross-Origin Resource Sharing vulnerabilities.

---

## 📚 Supported Sites

| Site | Method | Technology | Notes |
|------|--------|------------|-------|
| **Z-TMO** | Crawler + Cascade | **Crawl4AI** | Supports full series and single chapters. |
| **TMO-H** | AI Extraction | **Crawl4AI** | Intelligent image detection. |
| **M440** | Crawler | **Crawl4AI** | Supports covers and chapters. |
| **H2R**| JSON Parsing | **AsyncIO** | Fast metadata extraction. |
| **Hentalk.pw** | SvelteKit JSON | **Playwright** | Bypasses Cloudflare & parses native JSON. |
| **Hi.la** | Batch JS Injection| **Playwright** | Fast URL extraction + Load Balancing + Retry logic. |
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
    DISCORD_TOKEN=your_discord_token  # Optional for Discord Bot
    HEADLESS=true                     # Optional Playwright Visibility (Default: false)
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
2.  The browser will open automatically at `http://localhost:3000`.
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

## 🧩 Extensibility (Adding New Sites)
MangaDownloader is designed to be easily extensible using the Strategy Pattern. To add support for a new website:
1. Create a new handler class inheriting from `BaseSiteHandler` in `core/sites/`.
2. Implement `get_supported_domains()` and the `process()` logic.
3. Register the handler in the `HANDLERS` list within `core/handler.py`.
> For detailed instructions on how an AI or Developer should analyze the DOM and build a new module, refer to the [HOW_TO_ADD_SITES.md](HOW_TO_ADD_SITES.md) guide.

---

## 📂 Project Structure

```
MangaDownloader/
├── core/                   # Refactored Core Package
│   ├── sites/              # Site Handlers (Strategy Pattern)
│   ├── config.py           # Configuration
│   ├── handler.py          # Routing Logic
│   └── utils.py            # PDF & Download Utils
├── web_client_next/        # Next.js Frontend Dashboard
├── app.py                  # Legacy Tkinter GUI
├── bot.py                  # Discord Bot
├── web_server.py           # FastAPI Backend
└── PDF/                    # Output Directory
```

## 🐳 Docker (Production Ready)

Run the entire complete stack instantly using Docker containers:
```bash
docker-compose up --build -d
```
*   **Frontend (Next.js):** `http://localhost:3000`
*   **Backend (FastAPI):** `http://localhost:8000`
