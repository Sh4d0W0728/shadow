@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0update-shadow.ps1"
set "SHADOW_EXIT=%ERRORLEVEL%"
if not "%SHADOW_EXIT%"=="0" echo Update failed. Please copy the error above.
pause
exit /b %SHADOW_EXIT%
