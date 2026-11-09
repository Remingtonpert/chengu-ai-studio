@echo off
rem 控制台代码页切到 UTF-8，否则 Python 打中文/emoji 会 UnicodeEncodeError
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0release_local_engine.ps1"
