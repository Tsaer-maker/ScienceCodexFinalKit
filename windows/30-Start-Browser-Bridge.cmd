@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Switchboard.ps1" -Action browser-science
set "result=%errorlevel%"
if not "%result%"=="0" pause
exit /b %result%
