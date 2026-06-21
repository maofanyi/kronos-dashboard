@echo off
cd /d %~dp0

echo === Kronos Dashboard ===
echo.

REM Start sync service
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath python -ArgumentList 'sync_service\main.py' -WorkingDirectory '%~dp0' -WindowStyle Hidden"

REM Wait for SQLite init
timeout /t 2 >nul

REM Start API + Web server
python api\server.py
