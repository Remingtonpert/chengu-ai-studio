@echo off
rem 控制台代码页切到 UTF-8，否则 Python 打中文/emoji 会 UnicodeEncodeError
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
rem Run the REAL SD image acceptance (txt2img + img2img) with auto-wait for free VRAM.
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0acceptance_sd.ps1"
