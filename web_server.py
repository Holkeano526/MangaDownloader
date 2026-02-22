
"""
Module: web_server
Description: The primary FastAPI backend server for the Manga Downloader Web Client.
It provides HTTP endpoints for serving generated PDFs safely and a WebSocket 
interface for real-time progress updates, logs, and process management (start/cancel).
It includes security middleware for CORS, Path Traversal (LFI) prevention, 
and simple DoS rate limiting.
"""
import os
import sys
import asyncio
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from typing import List
from urllib.parse import unquote
from fastapi.responses import FileResponse
import core
import core.config

# Disable auto-opening of files on server side
core.config.OPEN_RESULT_ON_FINISH = False

app = FastAPI()

# [SEGURIDAD - OPEN SOURCE]
# Mitigación de vulnerabilidad CORS (Cross-Origin Resource Sharing).
# Evitar el uso de allow_origins=["*"] junto con allow_credentials=True, ya que 
# permitiría a cualquier página web maliciosa externa conectarse al servidor local del usuario.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173", "http://127.0.0.1:3000", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# Serve PDF directory manually to ensure CORS/Fetch works flawlessly
# We assume PDF folder is in CWD as per utils.py default
pdf_dir = os.path.join(os.getcwd(), core.config.PDF_FOLDER_NAME)
if not os.path.exists(pdf_dir):
    os.makedirs(pdf_dir)

@app.get("/pdfs/{filename:path}")
async def get_pdf(filename: str):
    filename = unquote(filename)
    
    # [SEGURIDAD - OPEN SOURCE]
    # Prevención de Path Traversal / Local File Inclusion (LFI).
    # Este bloque asegura que un atacante no pueda inyectar secuencias como '../../'
    # en la URL para leer archivos del sistema (ej. contraseñas, código fuente).
    # Se fuerza al sistema operativo a resolver la ruta absoluta real y se verifica
    # matemáticamente que dicha ruta nazca OBLIGATORIAMENTE desde la carpeta 'pdf_dir'.
    target_path = os.path.abspath(os.path.join(pdf_dir, filename))
    if not target_path.startswith(os.path.abspath(pdf_dir)):
        print(f"SECURITY WARNING: Attempted path traversal for '{filename}'. Blocked.")
        return {"error": "Invalid file path requested."}
    
    print(f"DEBUG: Request for PDF. Filename='{filename}'. Path='{target_path}'")
    
    if os.path.exists(target_path) and os.path.isfile(target_path):
        print("DEBUG: File found. Serving...")
        response = FileResponse(target_path, media_type="application/pdf")
        response.headers["Content-Disposition"] = "inline"
        return response
    
    print("DEBUG: File NOT found.")
    return {"error": "File not found."}

# Basic DoS Protection: Limit active downloads
ACTIVE_DOWNLOADS = 0
MAX_DOWNLOADS = 3

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    global ACTIVE_DOWNLOADS
    await websocket.accept()
    
    cancel_event = asyncio.Event()
    is_cancelled = False
    
    def check_cancel():
        return is_cancelled
    
    try:
        while True:
            try:
                data = await websocket.receive_json()
            except WebSocketDisconnect:
                break
                
            command = data.get("command")
            
            if command == "start":
                url = data.get("url")
                if not url:
                    await websocket.send_json({"type": "error", "message": "No URL provided"})
                    continue
                
                # SECURITY PATCH: DoS / Resource Exhaustion Protection
                if ACTIVE_DOWNLOADS >= MAX_DOWNLOADS:
                    await websocket.send_json({"type": "error", "message": "Server is currently busy. Please try again later."})
                    continue
                
                ACTIVE_DOWNLOADS += 1
                is_cancelled = False
                await websocket.send_json({"type": "status", "status": "running"})
                
                generated_pdf_name = []

                def log_callback(msg):
                    # Updated to match core.py new log format
                    if "[SUCCESS] PDF Generated:" in msg:
                        try:
                            # Parse filename from log
                            name = msg.split("PDF Generated:")[1].strip()
                            generated_pdf_name.append(name)
                        except: pass
                    
                    # Create task to safely send message
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        loop.create_task(websocket.send_json({"type": "log", "message": msg}))
                    else:
                        asyncio.run(websocket.send_json({"type": "log", "message": msg}))
                    
                def progress_callback(current, total):
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                         loop.create_task(websocket.send_json({
                            "type": "progress", 
                            "current": current, 
                            "total": total
                        }))

                try:
                    await core.process_entry(
                        url, 
                        log_callback, 
                        check_cancel, 
                        progress_callback=progress_callback
                    )
                    
                    final_filename = generated_pdf_name[0] if generated_pdf_name else None
                    await websocket.send_json({
                        "type": "status", 
                        "status": "completed",
                        "filename": final_filename
                    })
                except Exception as e:
                    # SECURITY PATCH: Information Leakage Prevention
                    # Log the actual error to the console, send a sanitized message to the client
                    logging.error(f"Internal processing error: {e}")
                    await websocket.send_json({"type": "error", "message": "An unexpected internal error occurred during processing."})
                    await websocket.send_json({"type": "status", "status": "error"})
                finally:
                    ACTIVE_DOWNLOADS = max(0, ACTIVE_DOWNLOADS - 1)
            
            elif command == "cancel":
                is_cancelled = True
                await websocket.send_json({"type": "log", "message": "Cancelling..."})
                
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        try:
            await websocket.close()
        except: pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
