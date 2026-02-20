
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
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
    file_path = os.path.join(pdf_dir, filename)
    print(f"DEBUG: Request for PDF. Filename='{filename}'. Path='{file_path}'")
    
    if os.path.exists(file_path):
        print("DEBUG: File found. Serving...")
        response = FileResponse(file_path, media_type="application/pdf")
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "*"
        response.headers["Content-Disposition"] = "inline"
        return response
    
    print("DEBUG: File NOT found.")
    return {"error": f"File not found: {filename}"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
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
                    await websocket.send_json({"type": "error", "message": str(e)})
                    await websocket.send_json({"type": "status", "status": "error"})
            
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
