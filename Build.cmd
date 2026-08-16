@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows\Switchboard.ps1" -Action build
set "result=%errorlevel%"
pause
exit /b %result%
