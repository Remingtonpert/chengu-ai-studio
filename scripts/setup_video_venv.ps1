# 创建/重建 .venv-video —— Wan2.1 视频引擎（wan-t2v / wan-i2v）的独立虚拟环境。
#
# 包版本与 .venv-image 完全一致（torch cu130 三件套 + diffusers 0.41 + transformers 5.19），
# 本可共用环境，但按用户要求视频引擎独立成 .venv-video，与图片/音乐环境完全隔离；
# 另加 imageio/imageio-ffmpeg（mp4 导出）与 sentencepiece（UMT5 tokenizer 兜底）。
#
# 用法：pwsh -File scripts\setup_video_venv.ps1   （可重复执行，幂等）

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $root ".venv-video"

if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    Write-Host "== 创建 .venv-video (Python 3.11) =="
    py -3.11 -m venv $venv
    if ($LASTEXITCODE -ne 0) { Write-Error "venv 创建失败" }
}
$py = Join-Path $venv "Scripts\python.exe"

Write-Host "== [1/3] 升级 pip / wheel =="
& $py -m pip install --upgrade pip wheel
if ($LASTEXITCODE -ne 0) { Write-Error "pip 升级失败" }

Write-Host "== [2/3] PyTorch cu130 三件套（与 .venv-image 同版本，本机 RTX 3060 已验证） =="
& $py -m pip install torch==2.14.1+cu130 torchvision==0.29.1+cu130 torchaudio==2.11.0+cu130 --index-url https://download.pytorch.org/whl/cu130
if ($LASTEXITCODE -ne 0) { Write-Error "PyTorch cu130 安装失败" }

Write-Host "== [3/3] diffusers 视频链路（与 .venv-image 同版本钉死） + mp4 导出 =="
& $py -m pip install diffusers==0.41.0 transformers==5.19.0 accelerate==1.15.0 safetensors==0.8.0 huggingface_hub==1.33.0 sentencepiece imageio imageio-ffmpeg
if ($LASTEXITCODE -ne 0) { Write-Error "diffusers 链路安装失败" }

Write-Host "== 验证导入与 CUDA =="
& $py -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available()); from diffusers import WanPipeline, WanVACEPipeline; print('WanPipeline/WanVACEPipeline OK')"
if ($LASTEXITCODE -ne 0) { Write-Error "导入验证失败（见上方报错）" }

Write-Host "== .venv-video 就绪 =="
