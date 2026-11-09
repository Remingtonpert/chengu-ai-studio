@echo off
rem 控制台代码页切到 UTF-8，否则 Python 打中文/emoji 会 UnicodeEncodeError
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
rem ACE-Step music acceptance runner (double-click).
where pwsh >nul 2>nul && (pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0acceptance_music.ps1" %*) || (powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0acceptance_music.ps1" %*)
echo.
pause
