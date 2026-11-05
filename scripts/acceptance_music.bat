@echo off
rem ACE-Step music acceptance runner (double-click).
where pwsh >nul 2>nul && (pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0acceptance_music.ps1" %*) || (powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0acceptance_music.ps1" %*)
echo.
pause
