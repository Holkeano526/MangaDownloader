"""
Configuration and constants for the Manga Downloader application.
"""
import os

# ==============================================================================
# API CONFIGURATION
# ==============================================================================

# Load API key from environment variable (more secure than hardcoding)
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "AIzaSyBXzU2iIbOTWjiPsGyuXeT3aRwDampVps0")

# ==============================================================================
# BROWSER CONFIGURATION
# ==============================================================================

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# ==============================================================================
# SITE-SPECIFIC HEADERS
# ==============================================================================

HEADERS_TMO = {
    "Referer": "https://tmohentai.com/",
    "User-Agent": USER_AGENT
}

HEADERS_M440 = {
    "Referer": "https://m440.in/",
    "User-Agent": USER_AGENT
}

HEADERS_H2R = {
    "Referer": "https://hentai2read.com/",
    "User-Agent": USER_AGENT
}

HEADERS_HITOMI = {
    "Referer": "https://hitomi.la/",
    "User-Agent": USER_AGENT
}

# ==============================================================================
# FOLDER CONFIGURATION
# ==============================================================================

TEMP_FOLDER_NAME = "temp_manga_images"
PDF_FOLDER_NAME = "PDF"

# ==============================================================================
# DOWNLOAD CONFIGURATION
# ==============================================================================

BATCH_SIZE = 10
DEFAULT_PAGE_COUNT = 60

# ==============================================================================
# SUPPORTED DOMAINS
# ==============================================================================

SUPPORTED_DOMAINS = ["tmohentai", "m440.in", "mangas.in", "hentai2read", "hitomi.la"]
