@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0FinalKit.ps1" -Action update-runtime %*
set "result=%errorlevel%"
if not "%result%"=="0" pause
exit /b %result%
