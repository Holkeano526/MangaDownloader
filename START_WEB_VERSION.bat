@echo off
echo Installing Python dependencies...
pip install -r requirements.txt

echo Starting Backend Server...
start "Manga Downloader Backend" cmd /k "python web_server.py"

echo Starting Frontend...
cd web_client_next
start "Manga Downloader Frontend" cmd /k "npm run dev"

echo Waiting for services to start...
timeout /t 5

echo Opening Browser...
start http://localhost:3000

echo Done! Two windows should have opened. One for the backend and one for the frontend.
pause
