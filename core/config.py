
import os
from dotenv import load_dotenv

load_dotenv()

# API Keys and Tokens
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    print("[WARN] GOOGLE_API_KEY not found in .env")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# Browser Identity
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Headers
HEADERS_TMO = {"Referer": "https://tmohentai.com/", "User-Agent": USER_AGENT}
HEADERS_M440 = {"Referer": "https://m440.in/", "User-Agent": USER_AGENT}
HEADERS_H2R = {"Referer": "https://hentai2read.com/", "User-Agent": USER_AGENT}
HEADERS_HITOMI = {"Referer": "https://hitomi.la/", "User-Agent": USER_AGENT}
HEADERS_ZONATMO = {"Referer": "https://zonatmo.com/", "User-Agent": USER_AGENT}
HEADERS_NHENTAI = {"User-Agent": USER_AGENT} # Adding explicit nhentai header

# Folder Names
TEMP_FOLDER_NAME = "temp_manga_images"
PDF_FOLDER_NAME = "PDF"

# Download Settings
BATCH_SIZE = 10            # (obsoleto) antes se descargaba en tandas fijas de 10
# Descargas simultáneas de imágenes POR manga. Antes eran tandas de 10 que esperaban
# a la más lenta (y en Hitomi un semáforo GLOBAL de 2 para todo el bot).
# Medido en Hitomi (89 páginas): 2 -> 28,8 s; 3 -> 21,1 s; 4 -> 20,4 s; 6 -> 22,9 s con 51
# reintentos (el servidor limita con 503/429). 3 es el mejor equilibrio.
CONCURRENCIA_IMAGENES = int(os.getenv("CONCURRENCIA_IMAGENES", "3"))
REINTENTOS_IMAGEN = 4      # 429/5xx/timeouts: reintento con espera creciente
# Calidad JPEG al convertir WebP/AVIF para el PDF (img2pdf no admite WebP).
# 70 = igual que siempre. 80 mejora algo la imagen: +0,6 s y +19 % de tamaño (medido).
JPEG_CALIDAD = int(os.getenv("JPEG_CALIDAD", "70"))
DEFAULT_PAGE_COUNT = 60

# Runtime Flags
OPEN_RESULT_ON_FINISH = True
