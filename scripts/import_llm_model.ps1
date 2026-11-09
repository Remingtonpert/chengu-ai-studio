# import_llm_model.ps1 —— 把本机 Ollama 模型权重导入项目内（离线/便携用）
#
# 为什么需要它：AI 智能体板块的聊天与文案能力必须真的用大模型，而不是「降级成规则回复」。
# 项目只负责调用 OpenAI 兼容端点（默认 Ollama http://127.0.0.1:11434/v1），权重本身不在项目里。
# 这个脚本按 manifest 精确复制某个模型的权重到项目 models\llm\ollama_models\，
# 换机/做便携包时再用 -Restore 放回目标机的 Ollama 模型库，全程不联网。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\import_llm_model.ps1 -Model qwen3.5:9b
#   powershell -ExecutionPolicy Bypass -File scripts\import_llm_model.ps1 -List
#   powershell -ExecutionPolicy Bypass -File scripts\import_llm_model.ps1 -Model qwen3.5:9b -Restore
param(
    [string]$Model = "qwen3.5:9b",
    [string]$Source = "D:\OllamaModels",
    [string]$Dest = "",
    [switch]$List,
    [switch]$Restore,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $Dest) { $Dest = Join-Path $root "models\llm\ollama_models" }

function Get-ManifestPath([string]$store, [string]$model) {
    # qwen3.5:9b -> manifests\registry.ollama.ai\library\qwen3.5\9b
    $family, $tag = $model -split ":", 2
    if (-not $tag) { $tag = "latest" }
    return (Join-Path $store "manifests\registry.ollama.ai\library\$family\$tag")
}

$projectStore = $Dest
$ollamaStore = if ($env:OLLAMA_MODELS) { $env:OLLAMA_MODELS } else { $Source }

# ------------------------------------------------------------------ 列出可用模型
if ($List) {
    "项目内已导入的模型（$projectStore）："
    $pm = Join-Path $projectStore "manifests\registry.ollama.ai\library"
    if (Test-Path $pm) {
        Get-ChildItem $pm -Recurse -File | ForEach-Object {
            $rel = $_.FullName.Substring($pm.Length + 1) -replace "\\", "/"
            "  " + ($rel -replace "/", ":")
        }
    } else { "  （空）" }
    ""
    "源模型库（$ollamaStore）："
    $sm = Join-Path $ollamaStore "manifests\registry.ollama.ai\library"
    if (Test-Path $sm) {
        Get-ChildItem $sm -Recurse -File | ForEach-Object {
            $rel = ($_.FullName.Substring($sm.Length + 1) -replace "\\", "/")
            $name = ($rel -replace "/", ":")
            $mf = Get-Content $_.FullName -Raw | ConvertFrom-Json
            $total = 0
            foreach ($l in @($mf.config) + @($mf.layers)) { if ($l.size) { $total += [int64]$l.size } }
            "  {0,-28} {1,7:N2} GB" -f $name, ($total / 1GB)
        }
    } else { "  （没找到 manifests）" }
    return
}

# ------------------------------------------------------------------ 复制方向
if ($Restore) {
    # 把项目内的权重放回目标机的 Ollama 模型库（换机后执行一次）
    if (-not (Test-Path $projectStore)) { throw "项目内没有已导入的模型：$projectStore" }
    if (-not (Test-Path $ollamaStore)) { throw "目标模型库不存在：$ollamaStore（先启动一次 Ollama，或设 OLLAMA_MODELS）" }
    "从 $projectStore 恢复到 $ollamaStore"
    robocopy $projectStore $ollamaStore /E /XC /XN /XO /NJH /NJS /NDL /NFL | Out-Null
    "完成。ollama list 应能看到模型；随后在设置里把 llm_model 填成同名模型。"
    return
}

# ------------------------------------------------------------------ 导入到项目
if (-not (Test-Path $Source)) { throw "源模型库不存在：$Source" }
$srcManifest = Get-ManifestPath $Source $Model
if (-not (Test-Path $srcManifest)) { throw "模型不存在：$Model（用 -List 看有哪些）" }

$family, $tag = $Model -split ":", 2
if (-not $tag) { $tag = "latest" }
$dstManifestDir = Join-Path $projectStore "manifests\registry.ollama.ai\library\$family"
$dstBlobs = Join-Path $projectStore "blobs"
New-Item -ItemType Directory -Force -Path $dstManifestDir | Out-Null
New-Item -ItemType Directory -Force -Path $dstBlobs | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $projectStore "metadata") | Out-Null

$mf = Get-Content $srcManifest -Raw | ConvertFrom-Json
$digests = @()
if ($mf.config.digest) { $digests += $mf.config.digest }
foreach ($l in $mf.layers) { if ($l.digest) { $digests += $l.digest } }

$totalBytes = [int64]0
$missing = @()
"Copied digests for $Model"
foreach ($d in $digests) {
    $hex = ($d -split ":", 2)[1]
    $blobName = "sha256-$hex"
    $srcBlob = Join-Path $Source "blobs\$blobName"
    if (-not (Test-Path $srcBlob)) {
        # Ollama 也可能把内容存在 manifests-v2 旁边的小写形式，先按前缀再找一次
        $alt = Get-ChildItem (Join-Path $Source "blobs") -Filter "$blobName*" -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -eq $blobName } | Select-Object -First 1
        if (-not $alt) { $missing += $blobName; "  MISSING  $blobName"; continue }
        $srcBlob = $alt.FullName
    }
    $item = Get-Item $srcBlob
    $totalBytes += $item.Length
    $dstBlob = Join-Path $dstBlobs $blobName
    if ((Test-Path $dstBlob) -and (-not $Force)) {
        $dlen = (Get-Item $dstBlob).Length
        if ($dlen -eq $item.Length) { "  SKIP     $blobName ($([math]::Round($item.Length/1GB,2)) GB 已存在)" ; continue }
    }
    "  COPY     $blobName ($([math]::Round($item.Length/1GB,2)) GB)"
    Copy-Item -LiteralPath $srcBlob -Destination $dstBlob -Force
    $metaSrc = Join-Path $Source "metadata\$blobName.json"
    if (Test-Path $metaSrc) { Copy-Item -LiteralPath $metaSrc -Destination (Join-Path $projectStore "metadata\$blobName.json") -Force }
}
if ($missing.Count) {
    throw "缺少 blob：$($missing -join ', ') —— 该模型在源库里不完整（可能还在下载），换一个模型或先 ollama pull 完成。"
}

Copy-Item -LiteralPath $srcManifest -Destination (Join-Path $dstManifestDir $tag) -Force
$report = [ordered]@{
    model = $Model
    source_store = $Source
    project_store = $projectStore
    total_bytes = $totalBytes
    total_gb = [math]::Round($totalBytes / 1GB, 2)
    digests = $digests
    imported_at = (Get-Date).ToString("s")
}
$report | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $projectStore "imported-$($Model -replace ':', '_').json") -Encoding UTF8

""
"导入完成：$Model 共 $([math]::Round($totalBytes/1GB,2)) GB -> $projectStore"
"验证：ollama list 里应已有 $Model；项目 config.json 的 llm_model 填成同名模型。"
"换机后恢复：powershell -File scripts\import_llm_model.ps1 -Model $Model -Restore"




