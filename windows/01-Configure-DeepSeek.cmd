@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Switchboard.ps1" -Action configure-deepseek
set "result=%errorlevel%"
pause
exit /b %result%
