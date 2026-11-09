# run_web.ps1 —— 启动「辰鹄AI创作中心」网页控制台
#
# 网页端和桌面端必须同时存在：两端共用同一条 SQLite FIFO 任务队列与执行者锁，
# 起网页端不会影响桌面端，桌面端也不需要先关掉。这里只解决「网页端起不来 / 起不来还不知道为什么」。
#
# 用法（在项目根目录或任意位置）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_web.ps1            # 没跑就启动，已在跑就直接打开浏览器
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_web.ps1 -Restart   # 改了代码后重启（只杀本项目自己起的进程）
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_web.ps1 -Status     # 只看状态，不动进程
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_web.ps1 -Foreground # 前台运行，日志直接打在终端里
param(
    [int]$Port = 0,
    [string]$BindHost = "",
    [switch]$Restart,
    [switch]$Status,
    [switch]$NoBrowser,
    [switch]$Foreground
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $ProjectRoot "logs\manual_console"

if (-not (Test-Path $Python)) {
    throw "项目虚拟环境不存在。请先运行: py -3.11 -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
}

# ---------------------------------------------------------------- 端口取配置里的值
# 网页端的端口写在 config.json（web_port），默认 127.0.0.1:8765；写死 8765 会在改过端口的机器上
# 报「端口被占用」，还顺带把别人的服务当成自己。
$configFile = Join-Path $env:LOCALAPPDATA "ChenguAIStudio\config.json"
$cfgPort = 8765
if (Test-Path $configFile) {
    try {
        $cfg = Get-Content $configFile -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($cfg.web_port) { $cfgPort = [int]$cfg.web_port }
        if (-not $BindHost -and $cfg.web_host) { $BindHost = [string]$cfg.web_host }
    } catch {
        Write-Warning "读不动 $configFile，用默认端口 8765：$_"
    }
}
if ($Port -le 0) { $Port = $cfgPort }
if (-not $BindHost) { $BindHost = "127.0.0.1" }
$url = "http://127.0.0.1:$Port/"

function Get-ListenerPids([int]$port) {
    (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
}

function Describe-Owner([int]$procId) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $procId" -ErrorAction SilentlyContinue
    if (-not $proc) { return $null }
    [pscustomobject]@{
        id       = $procId
        name     = $proc.Name
        command  = [string]$proc.CommandLine
        isOurs   = ($proc.CommandLine -match "app\.web\.server" -or ([string]$proc.ExecutablePath) -eq $Python)
    }
}

$listenerPids = @(Get-ListenerPids $Port)

if ($Status) {
    if (-not $listenerPids -or $listenerPids[0] -eq 0) {
        "端口 $Port 上没有服务在监听：网页端没起来。跑 scripts\run_web.ps1 启动。"
        return
    }
    "端口 $Port 正在监听（$url）："
    foreach ($procId in $listenerPids) {
        $owner = Describe-Owner $procId
        if ($owner) { "  PID {0}  {1}  {2}" -f $owner.id, $owner.name, $owner.command }
    }
    return
}

# ---------------------------------------------------------------- 已在跑：先处理旧实例
if ($listenerPids -and $listenerPids[0] -ne 0) {
    if (-not $Restart) {
        "网页端已经在运行：$url（PID $($listenerPids -join ', ')）"
        "  改了代码想让新代码生效：powershell -File scripts\run_web.ps1 -Restart"
        if (-not $NoBrowser) { Start-Process $url }
        return
    }
    foreach ($procId in $listenerPids) {
        $owner = Describe-Owner $procId
        if (-not $owner) { continue }
        if (-not $owner.isOurs) {
            throw "端口 $Port 被别的程序占着（PID $procId：$($owner.name)），不代杀。请改 config.json 的 web_port 或先自己关掉它。"
        }
        Stop-Process -Id $procId -Force
        "已停止旧的网页端 PID $procId"
        Start-Sleep -Milliseconds 600
    }
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

if ($Foreground) {
    "前台启动网页端：$url（Ctrl+C 停止；日志也会写进 $LogDir）"
    Push-Location $ProjectRoot
    try {
        & $Python -m app.web.server
        exit $LASTEXITCODE
    } finally {
        Pop-Location
    }
}

# ---------------------------------------------------------------- 后台启动
$out = Join-Path $LogDir "console.out.log"
$err = Join-Path $LogDir "console.err.log"
$proc = Start-Process -FilePath $Python `
    -ArgumentList "-m", "app.web.server" `
    -WorkingDirectory $ProjectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $out `
    -RedirectStandardError $err `
    -PassThru

$ready = $false
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500
    if ($proc.HasExited) { break }
    if (Get-ListenerPids $Port) { $ready = $true; break }
}

if (-not $ready) {
    if ($proc.HasExited) {
        "网页端启动失败：进程已退出（exit $($proc.ExitCode)）。最后几行错误："
        if (Test-Path $err) { Get-Content $err -Tail 12 }
    } else {
        "网页端 20 秒内没有在 $Port 上监听，进程还在（PID $($proc.Id)）。检查："
        if (Test-Path $err) { Get-Content $err -Tail 12 }
    }
    "完整日志：$out / $err"
    return
}

"网页端已启动：$url（PID $($proc.Id)）"
"  日志：$out"
"  停止：powershell -File scripts\run_web.ps1 -Restart 会先停旧再起新；或 Get-NetTCPConnection -LocalPort $Port -State Listen | Select OwningProcess 之后 Stop-Process"
"  桌面端不受影响，可以同时开着：powershell -File scripts\run_app.ps1"
if (-not $NoBrowser) { Start-Process $url }

