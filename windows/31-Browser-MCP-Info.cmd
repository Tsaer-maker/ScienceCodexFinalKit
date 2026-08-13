@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0FinalKit.ps1" -Action browser-mcp-info
set "result=%errorlevel%"
pause
exit /b %result%
