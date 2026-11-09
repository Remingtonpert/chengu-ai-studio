# agent_llm.ps1 —— 「辰鹄 AI 创作中心」「AI 智能体」自带大模型服务：装 / 起 / 停 / 看状态
#
# 三条硬边界（改坏了 tests\test_agent_and_i18n.py 会红，别顺手改掉）：
#   1) 只绑回环、端口 11435：不对局域网开放，也和用户自己 11434 上的 Ollama 互不干涉
#   2) 推理程序与权重都在项目内：tools\llama_cpp 与 models\llm\gguf，便携包整目录带走
#   3) 不依赖 Ollama：不读它的模型库、不碰它的进程，智能体的模型只来自项目内的 GGUF
#
<#
.SYNOPSIS
    起停「AI 智能体」用的本地大模型：项目自带 tools\llama_cpp\llama-server.exe
    + models\llm\gguf\Qwen3-8B-Q4_K_M.gguf，OpenAI 兼容 /v1，只监听 127.0.0.1:11435。

.DESCRIPTION
    默认加载参数（12GB 单卡实测出来的三条约束，改任何一条都要重新看显存）：
      --ctx-size 16384            上下文 16K（原来 8K 不够智能体串多轮）
      --cache-type-k / --cache-type-v q8_0
                                  KV cache 的键与值都量化，16K 的 KV 体积正好减半
      --fit on --fit-target 3686  按当下空闲显存自动决定塞多少层进 GPU，并且起完
                                  必须还留 3686 MiB（12288 的 30%）给生成侧；留不到
                                  就少塞几层（能跑但会慢），不会起手就 OOM
      --fit-ctx 16384             挡住「fit 为了塞进去偷偷把上下文砍小」
    程序自己也会按需拉起这个服务（config 的 llm_autostart），手动起只是图快。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\agent_llm.ps1 -Setup
    第一次装：下 llama.cpp 发布包与 GGUF 权重（都在项目内）→ 写 config → 起服务。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\agent_llm.ps1 -Start
    起服务（默认就是上面那三条显存约束）。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\agent_llm.ps1 -Stop
    停服务，只停 pid 文件里那个 llama-server.exe，把整块显存交还给生成引擎。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\agent_llm.ps1 -Status
    看状态：端口在听不等于能用，加载权重期间整个端点回 503。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\agent_llm.ps1 -Start -CtxSize 8192 -KvCacheType f16 -FreeVramTargetMib 0
    临时改加载参数（换小卡或显存被别的东西占满时）。
#>
param(
    [switch]$Setup,
    [switch]$Start,
    [switch]$Stop,
    [switch]$Status,
    [ValidateRange(1024, 65535)][int]$Port = 11435,
    [string]$ModelName = "qwen3:8b",
    [string]$Gguf = "Qwen3-8B-Q4_K_M.gguf",
    [string]$Release = "b12182",
    [int]$CtxSize = 16384,
    [ValidateSet("f16", "q8_0", "q4_0", "q4_1", "iq4_nl", "q5_0", "q5_1")]
    [string]$KvCacheType = "q8_0",
    [ValidateRange(0, 65535)][int]$FreeVramTargetMib = 3686,
    [int]$WaitSeconds = 240
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$root = Split-Path -Parent $PSScriptRoot
$toolDir = Join-Path $root "tools\llama_cpp"
$serverExe = Join-Path $toolDir "llama-server.exe"
$ggufDir = Join-Path $root "models\llm\gguf"
$ggufPath = if ([System.IO.Path]::IsPathRooted($Gguf)) { $Gguf } else { Join-Path $ggufDir $Gguf }
$logDir = Join-Path $root "logs\agent_llm"
$pidFile = Join-Path $logDir "llama_server.pid"
$api = "http://127.0.0.1:$Port"
$bindAddress = "127.0.0.1"     # 只绑回环：不对外开放，这是硬边界

# GitHub 直连在国内经常断，镜像列表按顺序试；curl 的 -C - 会在换源后续传已下载的部分。
$gitHubMirrors = @("https://gh-proxy.com/", "https://ghproxy.net/", "")
$hfHost = "https://hf-mirror.com"
$ggufRepo = "Qwen/Qwen3-8B-GGUF"

function Get-ListenerOwner {
    Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object { Get-CimInstance Win32_Process -Filter "ProcessId=$_" -ErrorAction SilentlyContinue }
}

function Query-Api([string]$suffix) {
    try { return (Invoke-RestMethod -Uri "$api$suffix" -TimeoutSec 8) } catch { return $null }
}

function Get-ToolZipName {
    # 有 nvidia-smi 就下 CUDA 包（本机实测明显快），没有就下 CPU 包：别装一套跑不动的。
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        "llama-$Release-bin-win-cuda-12.4-x64.zip"
    } else {
        "llama-$Release-bin-win-cpu-x64.zip"
    }
}

function Get-ToolZipUrls([string]$zip) {
    $origin = "https://github.com/ggml-org/llama.cpp/releases/download/$Release/$zip"
    $list = @()
    foreach ($prefix in $gitHubMirrors) { $list += "$prefix$origin" }
    return $list
}

function Get-GgufUrls([string]$file) {
    # hf-mirror 先行（国内直连可达），huggingface 兜底。
    @("$hfHost/$ggufRepo/resolve/main/$file", "https://huggingface.co/$ggufRepo/resolve/main/$file")
}

function Invoke-GatedDownload([string[]]$urls, [string]$dest, [string]$label) {
    foreach ($url in $urls) {
        "下载 $label"
        "  ← $url"
        & curl.exe -L --fail --retry 3 --retry-delay 2 -C - -o "$dest" "$url"
        if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $dest)) { return $true }
        "  这个源没成（curl 退出码 $LASTEXITCODE），换下一个源接着已下好的部分续传。"
    }
    return $false
}

function Update-AppConfig {
    # 端点 / 模型名 / 权重路径写进项目 config.json：走 app.core.config.ConfigStore，
    # 校验规则与界面里那套一模一样，不会写出界面读不懂的键。
    $python = Join-Path $root ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python)) {
        "没找到 $python，config 不代改：端点填 http://${bindAddress}:$Port/v1、模型名填 $ModelName 就行。"
        return
    }
    $code = @'
import sys
from app.core.config import ConfigStore
from app.core.paths import AppPaths
store = ConfigStore(AppPaths.from_environment())
config = store.load()
config['llm_endpoint'] = sys.argv[1]
config['llm_model'] = sys.argv[2]
config['llm_model_path'] = sys.argv[3]
store.save(config)
print('config -> ' + str(store.path))
'@
    Push-Location $root
    try { & $python -c $code "http://${bindAddress}:$Port/v1" $ModelName $ggufPath }
    finally { Pop-Location }
}

function Install-Everything {
    "第一次装：推理程序与权重都装进项目内，装完直接起服务。"
    if (-not (Test-Path -LiteralPath $serverExe)) {
        $zip = Get-ToolZipName
        $zipPath = Join-Path $env:TEMP $zip
        if (-not (Invoke-GatedDownload (Get-ToolZipUrls $zip) $zipPath "llama.cpp $Release（$zip）")) {
            throw "llama.cpp 发布包没下下来（镜像：$($gitHubMirrors -join ' / ')）。手工下载后解压到 $toolDir 也一样能用。"
        }
        New-Item -ItemType Directory -Force -Path $toolDir | Out-Null
        $expandDir = Join-Path $env:TEMP ("llama_expand_" + $Release)
        if (Test-Path -LiteralPath $expandDir) { Remove-Item -LiteralPath $expandDir -Recurse -Force }
        Expand-Archive -LiteralPath $zipPath -DestinationPath $expandDir -Force
        # llama-server.exe 本身只有 9 KB，真正的体量在同目录的运行库 DLL（ggml-cuda.dll 585 MB 级别）；
        # 少拷一个 DLL 就会「找不到 xxx.dll」，所以把它所在那一层目录整个拷进 tools\llama_cpp。
        $found = Get-ChildItem -LiteralPath $expandDir -Recurse -Filter "llama-server.exe" | Select-Object -First 1
        if (-not $found) {
            throw "解压完还是没找到 llama-server.exe（$toolDir）。官方包结构变了的话，手工把 llama-server.exe 与同目录的 dll 放进去。"
        }
        Copy-Item -LiteralPath (Join-Path $found.DirectoryName "*") -Destination $toolDir -Recurse -Force
        Remove-Item -LiteralPath $expandDir -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue
        "推理程序就位：$toolDir"
    } else {
        "推理程序已在：$serverExe"
    }
    if (-not (Test-Path -LiteralPath $ggufPath)) {
        New-Item -ItemType Directory -Force -Path $ggufDir | Out-Null
        $part = "$ggufPath.part"
        if (-not (Invoke-GatedDownload (Get-GgufUrls $Gguf) $part "GGUF 权重 $Gguf")) {
            throw "权重没下下来：$ggufPath（也可以自己下好丢进 $ggufDir，多个 GGUF 时取最大的那个）"
        }
        if ((Get-Item -LiteralPath $part).Length -lt 1GB) {
            Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue
            throw "下载到的文件不到 1 GB，多半是镜像返回的错误页，已丢弃。换个源再跑一次 -Setup。"
        }
        Move-Item -LiteralPath $part -Destination $ggufPath -Force
        "权重复核：$((Get-Item -LiteralPath $ggufPath).Length) 字节 → $ggufPath"
    } else {
        "权重已在：$ggufPath（$([math]::Round((Get-Item -LiteralPath $ggufPath).Length / 1GB, 2)) GB）"
    }
    Update-AppConfig
    return Start-LocalServer
}

function Start-LocalServer {
    if (-not (Test-Path -LiteralPath $serverExe)) { throw "没有推理程序：$serverExe（先跑一次 -Setup）" }
    if (-not (Test-Path -LiteralPath $ggufPath)) { throw "没有权重：$ggufPath（先跑一次 -Setup）" }
    $existing = @(Get-ListenerOwner)
    if ($existing.Count) {
        foreach ($owner in $existing) {
            $path = $owner.ExecutablePath
            if ($path -and $path.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
                "端口 $Port 上是项目里的旧实例（PID $($owner.ProcessId)），先停掉它再起干净的。"
                Stop-Process -Id $owner.ProcessId -Force -ErrorAction SilentlyContinue
                Start-Sleep -Seconds 2
            } else {
                throw "端口 $Port 被项目外的程序占着（PID $($owner.ProcessId)：$($owner.Name)）。换端口：scripts\agent_llm.ps1 -Port 11436，并把 config 的 llm_endpoint 一并改掉。"
            }
        }
    }
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    Update-AppConfig
    $outLog = Join-Path $logDir "llama_server.out.log"
    $errLog = Join-Path $logDir "llama_server.err.log"
    $serverArgs = @(
        "--model", "`"$ggufPath`"",
        "--alias", "`"$ModelName`"",
        "--host", $bindAddress,
        "--port", "$Port",
        "--ctx-size", "$CtxSize",
        "--cache-type-k", $KvCacheType,
        "--cache-type-v", $KvCacheType,
        "--fit", "on",
        "--fit-ctx", "$CtxSize",
        "--flash-attn", "on",
        "--jinja"
    )
    # 起完服务还得给生成侧留出显存：--fit-target 是硬性留白（12GB 卡的 30% = 3686 MiB），
    # 留不到 llama.cpp 就自己少塞几层进 GPU；填 0 表示不留白、能塞多满塞多满。
    if ($FreeVramTargetMib -gt 0) { $serverArgs += @("--fit-target", "$FreeVramTargetMib") }
    $proc = Start-Process -FilePath $serverExe -ArgumentList $serverArgs -WorkingDirectory $toolDir `
        -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog
    Set-Content -LiteralPath $pidFile -Value $proc.Id -Encoding ascii
    "正在起自带服务（PID $($proc.Id)，$bindAddress : $Port，模型名 $ModelName，上下文 $CtxSize，KV $KvCacheType，留白 $FreeVramTargetMib MiB）…"
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        $models = Query-Api "/v1/models"
        if ($models) {
            "自带服务已就绪（加载权重用时见日志：$errLog）。"
            foreach ($item in @($models.data)) { "  模型：$($item.id)" }
            Show-Vram
            return $true
        }
        if ($proc -and (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue) -eq $null) { break }
    }
    "起不来。日志尾部："
    foreach ($log in @($errLog, $outLog)) {
        if (Test-Path -LiteralPath $log) { "  --- $log ---"; Get-Content -LiteralPath $log -Tail 15 | ForEach-Object { "  " + $_ } }
    }
    throw "自带服务在 $WaitSeconds 秒内没就绪（端口 $Port）。上面是日志尾部，按里面说的改一行参数再试。"
}

function Stop-LocalServer {
    # 只停 pid 文件里、且镜像名确实是 llama-server.exe 的那个进程；端口上挂着别人的服务一律不动。
    $stopped = $false
    if (Test-Path -LiteralPath $pidFile) {
        $mine = 0
        $text = (Get-Content -LiteralPath $pidFile -Raw -ErrorAction SilentlyContinue)
        if ($text -and [int]::TryParse($text.Trim(), [ref]$mine) -and $mine -gt 0) {
            $proc = Get-Process -Id $mine -ErrorAction SilentlyContinue
            if ($proc) {
                if ($proc.Name -ne "llama-server") {
                    # Get-Process 报的进程名不带 .exe（带 .exe 的是 Win32_Process 那份），别在这儿卡住。
                    throw "pid 文件里的 PID $mine 现在跑的是 $($proc.Name)，不是 llama-server.exe，不碰它。"
                }
                Stop-Process -Id $mine -Force -ErrorAction SilentlyContinue
                "已停止自带大模型服务（PID $mine），显存当场交还给生成引擎。"
                $stopped = $true
            } else {
                "pid 文件里的 PID $mine 早就不在了，清掉记录。"
            }
        }
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    }
    if (-not $stopped) {
        $owners = @(Get-ListenerOwner)
        if (-not $owners.Count) {
            "自带服务本来就没在跑（端口 $Port 无监听），没什么要停的。"
            return $true
        }
        foreach ($owner in $owners) {
            if ($owner.Name -ne "llama-server.exe") {
                "端口 $Port 上跑的不是 llama-server.exe（PID $($owner.ProcessId)：$($owner.Name)），不管它。"
                continue
            }
            throw "端口 $Port 上项目外的 llama-server（PID $($owner.ProcessId)）不归这个脚本管：$($owner.ExecutablePath)"
        }
    }
    Show-Vram
    return $true
}

function Show-Vram {
    if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) { return }
    $lines = & nvidia-smi --query-gpu=memory.total,memory.used,memory.free --format=csv,noheader,nounits 2>$null
    foreach ($line in @($lines)) {
        if (-not $line) { continue }
        $parts = $line -split ","
        if ($parts.Count -lt 3) { continue }
        $total = [int]$parts[0].Trim(); $used = [int]$parts[1].Trim(); $free = [int]$parts[2].Trim()
        $verdict = if ($free -ge $FreeVramTargetMib) { "达标" } else { "不足" }
        "显存：整卡 $total MiB · 已用 $used MiB · 空闲 $free MiB（留白要求 $FreeVramTargetMib MiB：$verdict）"
    }
}

function Show-Status {
    "自带大模型服务状态（$bindAddress : $Port，模型名 $ModelName）"
    $mine = ""
    $pidText = if (Test-Path -LiteralPath $pidFile) { (Get-Content -LiteralPath $pidFile -Raw -ErrorAction SilentlyContinue) } else { $null }
    if ($pidText) { $mine = $pidText.Trim() }
    if ($mine) {
        $proc = Get-Process -Id ([int]$mine) -ErrorAction SilentlyContinue
        if ($proc) { "  pid 文件：PID $mine（$($proc.Name)）在跑" } else { "  pid 文件：PID $mine 已不在" }
    } else {
        "  没有 pid 文件（服务不是这个脚本起的，或者已经停过）"
    }
    $owners = @(Get-ListenerOwner)
    if ($owners.Count) {
        foreach ($owner in $owners) { "  端口 $Port 由 PID $($owner.ProcessId)（$($owner.Name)）在听：$($owner.ExecutablePath)" }
    } else {
        "  端口 $Port 无监听"
    }
    # 端口在听 ≠ 能用：llama-server 先绑端口、后加载权重，加载期间整个端点回 503。
    $models = Query-Api "/v1/models"
    if ($models) {
        "  ready=true（模型能用）"
        foreach ($item in @($models.data)) { "    模型：$($item.id)" }
        $props = Query-Api "/props"
        if ($props) {
            if ($props.n_ctx) { "    实际上下文 n_ctx = $($props.n_ctx)（训练上限 $($props.n_ctx_train)）" }
            if ($props.n_gpu_layers) { "    进 GPU 的层数 n_gpu_layers = $($props.n_gpu_layers)" }
            if ($props.model_path) { "    权重：$($props.model_path)" }
        }
    } elseif ($owners.Count) {
        "  running=true / ready=false：还在加载权重（一般 30-90 秒），这时端点回 503，不是坏了"
    } else {
        "  running=false：要起就 scripts\run_agent_llm.bat（或 scripts\agent_llm.ps1 -Start）；不想起也行，第一句聊天会自己把它拉起来"
    }
    Show-Vram
}

if ($Setup) { Install-Everything | Out-Null }
elseif ($Stop) { Stop-LocalServer | Out-Null }
elseif ($Status) { Show-Status }
else { Start-LocalServer | Out-Null }
