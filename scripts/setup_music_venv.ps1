# 创建/重建 .venv-music —— ACE-Step 1.5 音乐引擎的独立虚拟环境。
#
# 为什么单独一个 venv：官方要求 transformers>=4.51,<4.58，与图片/视频引擎
# 环境（.venv-image，transformers 5.x）存在硬冲突，必须环境隔离。
#
# torch 三件套不用官方钉的 2.7.1+cu128（Windows 上段错误 EXIT=139，上游 issue
# #1360），改用与 .venv-image 相同、已在本机 RTX 3060 验证稳定的 cu130 组合。
# triton / torchao / flash-attn / nano-vllm / gradio / REST 服务栈均不安装
# （Windows 不需要或我们不用对应路径）。
#
# 用法：pwsh -File scripts\setup_music_venv.ps1   （可重复执行，幂等）

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $root ".venv-music"
$repo = Join-Path $root "vendor\ACE-Step-1.5"

if (-not (Test-Path (Join-Path $repo "pyproject.toml"))) {
    Write-Error "未找到 vendor\ACE-Step-1.5，请先执行: git clone --depth 1 https://github.com/ace-step/ACE-Step-1.5 vendor\ACE-Step-1.5"
}

if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    Write-Host "== 创建 .venv-music (Python 3.11) =="
    py -3.11 -m venv $venv
    if ($LASTEXITCODE -ne 0) { Write-Error "venv 创建失败" }
}
$py = Join-Path $venv "Scripts\python.exe"

Write-Host "== [1/4] 升级 pip / wheel =="
& $py -m pip install --upgrade pip wheel
if ($LASTEXITCODE -ne 0) { Write-Error "pip 升级失败" }

Write-Host "== [2/5] 安装 ACE-Step 1.5 运行依赖（跳过 Windows 不需要/未用路径） =="
$deps = @(
    "transformers>=4.51.0,<4.58.0"
    "diffusers>=0.37.0"
    "accelerate>=1.12.0"
    "soundfile>=0.13.1"
    "loguru>=0.7.3"
    "einops>=0.8.1"
    "diskcache"
    "numba>=0.63.1"
    "vector-quantize-pytorch>=1.27.15"
    "torchcodec>=0.9.1"
    "toml"
    "peft>=0.18.0"
    "modelscope"
    "typer-slim>=0.21.1"
    "pytorch-wavelets>=1.3.0"
    "pywavelets>=1.9.0"
    "matplotlib>=3.7.5"
    "scipy>=1.10.1"
    "setuptools<72"
)
& $py -m pip install @deps
if ($LASTEXITCODE -ne 0) { Write-Error "依赖安装失败" }

# 依赖批次的 pip 回溯可能把 torch 降级成 PyPI 的 CPU 版（不带 +cu130 后缀时
# 解析器不认已装的 local version），因此依赖之后强制复位 cu130 三件套。
Write-Host "== [3/5] 强制复位 PyTorch cu130 三件套（与 .venv-image 相同版本） =="
& $py -m pip install --force-reinstall --no-deps torch==2.14.1+cu130 torchvision==0.29.1+cu130 torchaudio==2.11.0+cu130 --index-url https://download.pytorch.org/whl/cu130
if ($LASTEXITCODE -ne 0) { Write-Error "PyTorch cu130 复位失败" }

Write-Host "== [4/5] 以 --no-deps 可编辑安装 vendor\ACE-Step-1.5（保住上面钉好的 torch） =="
& $py -m pip install -e $repo --no-deps
if ($LASTEXITCODE -ne 0) { Write-Error "ace-step 安装失败" }

Write-Host "== 验证导入（torchcodec 需要 FFmpeg DLL，可能报缺库——如有请另行处理） =="
& $py -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available()); import acestep.handler; print('acestep.handler OK')"
if ($LASTEXITCODE -ne 0) { Write-Error "导入验证失败（见上方报错）" }

Write-Host "== .venv-music 就绪 =="
