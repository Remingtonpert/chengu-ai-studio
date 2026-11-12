# 创建/重建 .venv-image —— 文生图引擎 sana-multiling（Sana-1.6B 多语言版）的独立虚拟环境。
#
# 2026-11-10 换型说明：文生图已从「Wan2.1 单帧档（wan-t2i）」换成 Sana-1.6B 多语言版
# （代码 app/engine/image/sana.py，权重 models\sana-multiling，9.06GB）。这个环境以前是给
# SD1.5 用的，包版本沿用不变（torch cu130 + diffusers 0.41 + transformers 5.19 已在 RTX 3060
# 上验证过），只是现在服务的是 Sana。
#
# 为什么单独一个 venv：主进程 .venv 只装 PySide6（界面层绝不 import torch），真正的生成
# 各自跑在独立子进程里（.venv-image 出图、.venv-video 出图生图/视频、.venv-music 出音乐），
# 这样切换板块不会把几套权重同时搬进显存。
#
# 用法：pwsh -File scripts\setup_image_venv.ps1   （可重复执行，幂等）
# 装完接着下权重：.\.venv-image\Scripts\python.exe scripts\download_sana_weights.py

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $root ".venv-image"

if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    Write-Host "== 创建 .venv-image (Python 3.11) =="
    py -3.11 -m venv $venv
    if ($LASTEXITCODE -ne 0) { Write-Error "venv 创建失败" }
}
$py = Join-Path $venv "Scripts\python.exe"

Write-Host "== [1/3] 升级 pip / wheel =="
& $py -m pip install --upgrade pip wheel
if ($LASTEXITCODE -ne 0) { Write-Error "pip 升级失败" }

Write-Host "== [2/3] PyTorch cu130（Sana 只需 torch，不需要 torchvision/torchaudio） =="
& $py -m pip install torch==2.14.1+cu130 --index-url https://download.pytorch.org/whl/cu130
if ($LASTEXITCODE -ne 0) { Write-Error "PyTorch cu130 安装失败" }

Write-Host "== [3/3] Sana 出图链路（diffusers 0.41 有 SanaPipeline / AutoencoderDC） =="
# sentencepiece：Gemma-2-2B-IT 编码器的分词器要用；缺了它 from_pretrained 会直接失败。
& $py -m pip install diffusers==0.41.0 transformers==5.19.0 accelerate==1.15.0 safetensors==0.8.0 huggingface_hub==1.33.0 "sentencepiece>=0.2" "Pillow>=10"
if ($LASTEXITCODE -ne 0) { Write-Error "diffusers 链路安装失败" }
# peft 只服务于留在盘上的 SD1.5 后备路径（LCM-LoRA），现役 Sana 不需要；要用后备再装：
#   & $py -m pip install peft==0.21.2

Write-Host "== 验证导入与 CUDA =="
& $py -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available()); from diffusers import SanaPipeline, AutoencoderDC; import sentencepiece; print('SanaPipeline/AutoencoderDC/sentencepiece OK')"
if ($LASTEXITCODE -ne 0) { Write-Error "导入验证失败（见上方报错）" }

Write-Host "== .venv-image 就绪。下一步下权重："
Write-Host "  & $py scripts\download_sana_weights.py"
Write-Host "== 自检："
Write-Host "  & $py scripts\verify_env.py"
