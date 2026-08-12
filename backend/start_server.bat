@echo off
cd /d "%~dp0"
echo ============================================
echo  AI Math Coach backend - self-healing start
echo  Logs are written to backend\logs\
echo  Close this window to STOP the server.
echo ============================================
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0start_server.ps1"
pause
