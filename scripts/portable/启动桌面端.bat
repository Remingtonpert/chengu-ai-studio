@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
set "PKG=%~dp0"
set "RUNTIME=%PKG%runtime\python311\python.exe"

if not exist "%RUNTIME%" (
  echo [错误] 包内运行时尚未就绪：缺少 runtime\python311\python.exe
  pause
  exit /b 1
)

"%RUNTIME%" "%PKG%scripts\fix_portable_paths.py"
if errorlevel 1 (
  echo [错误] 路径自适应失败，请按上方提示处理。
  pause
  exit /b 1
)

set "VENV=%PKG%.venv\Scripts\python.exe"
for /f "usebackq delims=" %%L in (`""%VENV%" scripts\check_hardware.py --emit-env"`) do set "%%L"
echo 评级：%CHENGUAI_HW_TIER%  禁用模态：[%CHENGUAI_DISABLE_MODALITIES%]（空为不禁用）

set "CHENGUAI_DATA_ROOT=%PKG%data"
set "CHENGUAI_APP_HOME=%PKG%data\apphome"
set "HF_HUB_OFFLINE=1"
set "HF_DATASETS_OFFLINE=1"
set "HF_HUB_DISABLE_TELEMETRY=1"
start "ChenguAI-Desktop" cmd /k ""%VENV%" -m app.bootstrap"
exit /b 0
