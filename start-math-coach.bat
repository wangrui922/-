@echo off
title AI Math Coach Launcher

set "ROOT=%~dp0"
set "PATH=D:\DevTools\NodeJS;%PATH%"
set "PATH=C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2;%PATH%"

echo ============================================
echo   AI Math Coach (XiaoYue) - Local Launcher
echo ============================================
echo.
echo Starting backend (FastAPI on 127.0.0.1:8000) ...
start "Backend" cmd /k "cd /d %ROOT%backend && .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"

echo Starting frontend (Vite on port 5174) ...
start "Frontend" cmd /k "cd /d %ROOT%student-web && npm run dev"

timeout /t 6 >nul
echo.
echo Backend : http://127.0.0.1:8000
echo Frontend: http://localhost:5174
echo.
echo PHONE: open the "Network" URL shown in the Frontend window
echo        (e.g. http://192.168.x.x:5174/ )
echo.
echo LOGIN: phone 13988491963   code 000000
echo.
echo Close the two windows to stop the servers.
pause
