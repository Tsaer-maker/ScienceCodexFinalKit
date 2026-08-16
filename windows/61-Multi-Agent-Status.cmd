@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Switchboard.ps1" -Action agents-status
set "result=%errorlevel%"
pause
exit /b %result%
