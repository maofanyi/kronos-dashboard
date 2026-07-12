@echo off
cd /d %~dp0
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\start_dashboard.ps1
