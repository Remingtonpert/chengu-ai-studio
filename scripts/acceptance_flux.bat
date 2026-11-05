@echo off
rem 辰鹄AI创作中心 FLUX acceptance runner (double-click).
where pwsh >nul 2>nul && (pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0acceptance_flux.ps1" %*) || (powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0acceptance_flux.ps1" %*)
echo.
pause
