@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows\FinalKit.ps1" -Action claude-menu
set "result=%errorlevel%"
if not "%result%"=="0" pause
exit /b %result%
