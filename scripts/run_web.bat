@echo off
rem 控制台代码页切到 UTF-8，否则 Python 打中文/emoji 会 UnicodeEncodeError
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
rem Launch the Chengu AI Studio WEB console (double-click to run).
rem Same queue / same SQLite as the desktop app: the desktop app can stay open.
setlocal
set "HERE=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%run_web.ps1" %*
echo.
pause
endlocal
