
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
import uuid
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

# Global Download Manager for Queue System
class DownloadManager:
    def __init__(self):
        self.queue = []
        self.connections = []
        self.is_processing = False
        self.current_cancel_flag = False

    async def broadcast(self):
        state = {"type": "queue_state", "queue": self.queue}
        # Create a copy to iterate safely
        for conn in list(self.connections):
            try:
                await conn.send_json(state)
            except:
                if conn in self.connections:
                    self.connections.remove(conn)

    async def add_url(self, url):
        item_id = uuid.uuid4().hex[:8]
        self.queue.append({
            "id": item_id,
            "url": url,
            "status": "pending",
            "filename": None,
            "progress": 0,
            "current": 0,
            "total": 100,
            "logs": []
        })
        await self.broadcast()
        if not self.is_processing:
            asyncio.create_task(self.process_queue())

    async def process_queue(self):
        self.is_processing = True
        while True:
            pending_items = [i for i in self.queue if i["status"] == "pending"]
            if not pending_items:
                break
            
            item = pending_items[0]
            item["status"] = "running"
            item["logs"].append("[INFO] Starting download...")
            self.current_cancel_flag = False
            await self.broadcast()

            def log_callback(msg):
                item["logs"].append(msg)
                if "[SUCCESS] PDF Generated:" in msg:
                    try:
                        item["filename"] = msg.split("PDF Generated:")[1].strip()
                    except: pass
                
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        loop.create_task(self.broadcast())
                    else:
                        asyncio.run(self.broadcast())
                except: pass

            def progress_callback(current, total):
                item["current"] = current
                item["total"] = total
                item["progress"] = int((current / total) * 100) if total else 0
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        loop.create_task(self.broadcast())
                except: pass

            def check_cancel():
                return self.current_cancel_flag or item.get("cancelled", False)

            try:
                await core.process_entry(item["url"], log_callback, check_cancel, progress_callback)
                if item.get("cancelled", False) or self.current_cancel_flag:
                    item["status"] = "cancelled"
                else:
                    item["status"] = "completed"
            except Exception as e:
                logging.error(f"Internal error: {e}")
                item["status"] = "error"
                item["logs"].append(f"ERROR: {str(e)}")
            
            await self.broadcast()
        
        self.is_processing = False

    async def cancel_item(self, item_id):
        for item in self.queue:
            if item["id"] == item_id:
                if item["status"] == "pending":
                    item["status"] = "cancelled"
                elif item["status"] == "running":
                    self.current_cancel_flag = True
                    item["status"] = "cancelled"
        await self.broadcast()

manager = DownloadManager()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    manager.connections.append(websocket)
    await websocket.send_json({"type": "queue_state", "queue": manager.queue})
    
    try:
        while True:
            data = await websocket.receive_json()
            command = data.get("command")
            
            if command == "start":
                url = data.get("url")
                if url:
                    await manager.add_url(url)
            
            elif command == "cancel":
                item_id = data.get("id")
                if item_id:
                    await manager.cancel_item(item_id)
                else:
                    manager.current_cancel_flag = True
                    await manager.broadcast()
                
    except WebSocketDisconnect:
        if websocket in manager.connections:
            manager.connections.remove(websocket)
    except Exception as e:
        print(f"WebSocket error: {e}")
        if websocket in manager.connections:
            manager.connections.remove(websocket)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
