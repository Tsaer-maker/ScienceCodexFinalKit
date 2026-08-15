@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0FinalKit.ps1" -Action windows-claude -RemainingArgs glm
set "result=%errorlevel%"
if not "%result%"=="0" pause
exit /b %result%
