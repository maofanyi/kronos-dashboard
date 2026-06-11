@echo off
cd /d %~dp0

echo === Kronos Dashboard ===
echo.

REM Start sync service
start "Kronos Sync" python sync_service\main.py

REM Wait for SQLite init
timeout /t 2 >nul

REM Start API + Web server
python api\server.py
