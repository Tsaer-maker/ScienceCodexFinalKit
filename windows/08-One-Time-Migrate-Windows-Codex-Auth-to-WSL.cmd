@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0FinalKit.ps1" -Action migrate-windows-codex-auth-to-wsl %*
set "result=%errorlevel%"
pause
exit /b %result%
