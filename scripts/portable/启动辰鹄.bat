@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
set "PKG=%~dp0"
set "RUNTIME=%PKG%runtime\python311\python.exe"

if not exist "%RUNTIME%" (
  echo [错误] 包内运行时尚未就绪：缺少 runtime\python311\python.exe
  echo 请确认整个目录包是完整复制的（不要只复制部分子目录）。
  pause
  exit /b 1
)

echo [1/3] 首次运行路径自适应（纯本地文本替换，无联网；已做过会自动跳过）...
"%RUNTIME%" "%PKG%scripts\fix_portable_paths.py"
if errorlevel 1 (
  echo [错误] 路径自适应失败，请按上方提示处理。
  pause
  exit /b 1
)

set "VENV=%PKG%.venv\Scripts\python.exe"

echo [2/3] 启动前置硬件检测（只读本机 nvidia-smi 与内存，不上报任何信息）...
for /f "usebackq delims=" %%L in (`""%VENV%" scripts\check_hardware.py --emit-env"`) do set "%%L"
echo     评级：%CHENGUAI_HW_TIER%  禁用模态：[%CHENGUAI_DISABLE_MODALITIES%]（空为不禁用）

echo [3/3] 启动本地 Web 控制台（全程 127.0.0.1，无云端依赖）：http://127.0.0.1:8765
set "CHENGUAI_DATA_ROOT=%PKG%data"
set "CHENGUAI_APP_HOME=%PKG%data\apphome"
set "HF_HUB_OFFLINE=1"
set "HF_DATASETS_OFFLINE=1"
set "HF_HUB_DISABLE_TELEMETRY=1"
start "ChenguAI-Web" cmd /k ""%VENV%" -m app.web.server"
timeout /t 3 /nobreak >nul
start "" http://127.0.0.1:8765
exit /b 0
