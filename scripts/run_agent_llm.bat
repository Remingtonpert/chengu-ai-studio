@echo off
rem 控制台代码页切到 UTF-8，否则 Python 打中文/emoji 会 UnicodeEncodeError
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
rem Chengu AI Studio - start the local LLM that powers the "AI Agent" tab.
rem Weights live inside the project (models\llm\gguf), inference runs with the bundled
rem tools\llama_cpp\llama-server.exe, and the server binds 127.0.0.1 only.
rem First run on a fresh machine:  agent_llm.ps1 -Setup   (downloads llama.cpp + the GGUF weights)
setlocal
set "HERE=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%agent_llm.ps1" %*
echo.
echo Done. Close this window or press a key.
echo (First time? Run:  powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%agent_llm.ps1" -Setup )
pause
