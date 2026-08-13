@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows\FinalKit.ps1" -Action clear
set "result=%errorlevel%"
pause
exit /b %result%
