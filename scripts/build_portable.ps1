# 辰鹄AI创作中心 · 一键便携打包脚本
#
# 产出 dist\ChenguAIStudio_Portable\ 完整目录包：应用代码 + 三个虚拟环境
# （.venv / .venv-image / .venv-music）+ 包内自带 CPython 运行时 + 全部已就位
# 的模型权重 + 启动 bat + 硬件分级检测 + 迁移说明文档。整个目录复制到装有
# NVIDIA 显卡的 Windows 电脑后，双击「启动辰鹄.bat」即可离线运行（首次启动
# 自动做路径自适应与硬件分级，全程不联网）。
#
# 用法（项目根目录执行；整包含权重约 60GB+，预留足够磁盘空间）：
#   pwsh -File scripts\build_portable.ps1                  # 完整打包
#   pwsh -File scripts\build_portable.ps1 -SkipModels      # 调试用：跳过权重复制
#   pwsh -File scripts\build_portable.ps1 -SkipVenvs       # 调试用：跳过虚拟环境复制
#   pwsh -File scripts\build_portable.ps1 -OutDir E:\包    # 指定输出目录

param(
    [string]$OutDir = "dist\ChenguAIStudio_Portable",
    [switch]$SkipModels,
    [switch]$SkipVenvs
)

$ErrorActionPreference = "Stop"
$root = (Get-Location).Path
$dist = if ([System.IO.Path]::IsPathRooted($OutDir)) { $OutDir } else { Join-Path $root $OutDir }

function RC($src, $dst, [string[]]$extraXd = @()) {
    if (-not (Test-Path $src)) { Write-Warning "跳过（不存在）：$src"; return }
    Write-Host "==> $src`n -> $dst"
    $xd = @('__pycache__', '.git', '.cache') + $extraXd
    robocopy $src $dst /MIR /MT:16 /R:1 /W:1 /NFL /NDL /NJH /NJS /XD @xd /XF '*.incomplete' | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy 失败（$src → $dst，exit=$LASTEXITCODE）" }
}

Write-Host "== 便携打包：$root`n-> $dist =="
New-Item -ItemType Directory -Force -Path $dist | Out-Null

# 1) 应用代码与脚本（不含 __pycache__）
RC (Join-Path $root 'app') (Join-Path $dist 'app')
RC (Join-Path $root 'scripts') (Join-Path $dist 'scripts')
RC (Join-Path $root 'vendor\ACE-Step-1.5') (Join-Path $dist 'vendor\ACE-Step-1.5')
Copy-Item (Join-Path $root 'README.md') (Join-Path $dist 'README.md') -Force

# 2) 包内自带 CPython 3.11 运行时（虚拟环境的 pyvenv.cfg home 指向它）
$basePy = (& (Join-Path $root '.venv\Scripts\python.exe') -c "import sys;print(sys.base_prefix)").Trim()
Write-Host "== 包内运行时：$basePy =="
RC $basePy (Join-Path $dist 'runtime\python311')

# 3) 三个虚拟环境（.venv 桌面/Web 主环境；.venv-image 图像/视频；.venv-music 音乐）
if (-not $SkipVenvs) {
    foreach ($v in '.venv', '.venv-image', '.venv-music') {
        RC (Join-Path $root $v) (Join-Path $dist $v)
    }
}

# 4) 模型权重（已下载多少带多少；缺的模块启动后对应引擎自动不注册）
if (-not $SkipModels) {
    $modelDirs = @('models\flux-schnell', 'models\ace-step-1.5', 'models\anima-image2video')
    foreach ($m in $modelDirs) {
        $p = Join-Path $root $m
        if (Test-Path $p) { RC $p (Join-Path $dist $m) } else { Write-Warning "权重缺失（该模块引擎将不注册）：$m" }
    }
}

# 5) 启动入口与文档（bat 模板 UTF-8 无 BOM + chcp 65001，已在脚本内处理）
Copy-Item (Join-Path $root 'scripts\portable\启动辰鹄.bat') (Join-Path $dist '启动辰鹄.bat') -Force
Copy-Item (Join-Path $root 'scripts\portable\启动桌面端.bat') (Join-Path $dist '启动桌面端.bat') -Force
Copy-Item (Join-Path $root 'scripts\portable\打包与迁移说明.md') (Join-Path $dist '打包与迁移说明.md') -Force

# 6) 打包清单：记录打包机绝对路径，供目标机首次启动的路径自适应脚本替换
$manifest = @{ source_root = $root; built_at = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') } | ConvertTo-Json
[System.IO.File]::WriteAllText((Join-Path $dist 'build_manifest.json'), $manifest + "`n", (New-Object System.Text.UTF8Encoding($false)))

# 7) 汇总
$size = (Get-ChildItem $dist -Recurse -File -ErrorAction SilentlyContinue | Measure-Object -Sum Length).Sum
Write-Host ("== 完成：{0}（约 {1:N1} GB）==" -f $dist, ($size / 1GB))
Write-Host '复制到目标电脑后双击「启动辰鹄.bat」：自动路径自适应 → 硬件分级 → 离线启动 Web 控制台。'
