@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0FinalKit.ps1" -Action windows-claude-configure -RemainingArgs glm
set "result=%errorlevel%"
pause
exit /b %result%
