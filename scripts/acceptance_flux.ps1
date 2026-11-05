# 辰鹄AI创作中心 FLUX real-hardware acceptance (Phase 3).
# Fully automated: restarts the local web console with HF_HUB_OFFLINE=1, runs
# end-to-end image generation, cancel (queued + running), worker-crash retry,
# console-restart recovery, and network isolation sampling. Writes a JSON
# summary + transcript into logs\acceptance_<timestamp>\. Never kills any
# process that does not belong to 辰鹄AI创作中心 (guards on command lines).
#
# Usage:  .\scripts\acceptance_flux.ps1            (full run)
#         .\scripts\acceptance_flux.ps1 -CheckOnly  (pre-flight checks only)
param(
    [switch]$CheckOnly,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$LogDir = Join-Path $Root "logs\acceptance_$Stamp"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
Start-Transcript -Path (Join-Path $LogDir 'transcript.log') | Out-Null

$VenvPy = Join-Path $Root '.venv\Scripts\python.exe'
$ImagePy = Join-Path $Root '.venv-image\Scripts\python.exe'
$Base = 'http://127.0.0.1:8765'
$GpuFreeMinMib = 10000
$RamFreeMinGb = 36

$Summary = [ordered]@{
    started_at = (Get-Date).ToString('o')
    root = $Root
    steps = [System.Collections.ArrayList]::new()
    gpu_used_peak_mib = 0
    worker_rss_peak_mib = 0
    external_connections = @()
    outputs = [System.Collections.ArrayList]::new()
    overall = 'pending'
}
function Save-Summary {
    $Summary | ConvertTo-Json -Depth 8 | Set-Content -Path (Join-Path $LogDir 'acceptance_summary.json') -Encoding UTF8
}
function Log($m) { Write-Host "[acceptance] $m" }
function Add-Step($name, $pass, $detail) {
    [void]$Summary.steps.Add([ordered]@{ name = $name; pass = [bool]$pass; detail = $detail })
    Save-Summary
    Log ("STEP {0}: {1} - {2}" -f $name, $(if ($pass) { 'PASS' } else { 'FAIL' }), $detail)
}

function Invoke-Api($method, $path, $body = $null) {
    $params = @{ Uri = "$Base$path"; Method = $method; TimeoutSec = 30 }
    if ($null -ne $body) {
        $params.Body = ($body | ConvertTo-Json -Depth 6 -Compress)
        $params.ContentType = 'application/json; charset=utf-8'
    }
    Invoke-RestMethod @params
}

function Stop-WebConsole {
    for ($round = 0; $round -lt 3; $round++) {
        $conns = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
        if (-not $conns) { return }
        foreach ($c in ($conns | Select-Object -Unique OwningProcess)) {
            $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($c.OwningProcess)" -ErrorAction SilentlyContinue
            if ($p -and $p.CommandLine -match 'app\.web\.server') {
                Log "stopping 辰鹄AI创作中心 web console PID=$($p.ProcessId)"
                Stop-Process -Id $p.ProcessId -Force
            }
        }
        # 轮询直到端口真正释放：旧进程退出可能超过 2 秒，竞态会让新控制台 bind 失败(10048)
        for ($i = 0; $i -lt 30; $i++) {
            if (-not (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue)) { Start-Sleep -Milliseconds 800; return }
            Start-Sleep -Milliseconds 500
        }
    }
    $still = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
    if ($still) {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$(($still | Select-Object -First 1).OwningProcess)" -ErrorAction SilentlyContinue
        throw "port 8765 still held by PID $($p.ProcessId) ($($p.Name): $($p.CommandLine)); refusing to continue"
    }
}

function Start-WebConsole {
    $env:HF_HUB_OFFLINE = '1'
    Start-Process -FilePath $VenvPy -ArgumentList '-m', 'app.web.server' -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir 'web_console.out.log') `
        -RedirectStandardError (Join-Path $LogDir 'web_console.err.log')
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        try { if ((Invoke-Api GET '/api/health').status -eq 'ok') { Log 'web console ready (HF_HUB_OFFLINE=1)'; return } } catch {}
    }
    throw 'web console did not become healthy within 30s'
}

function Get-FluxWorker {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match 'process_worker' -and $_.CommandLine -match 'flux_worker' }
}

function Submit-Image($prompt, $params) {
    Invoke-Api POST '/api/tasks' @{ task_type = 'image'; engine_id = 'flux-schnell'; prompt = $prompt; parameters = $params }
}

function Poll-Task($task, [int]$MaxSec, [hashtable]$Actions = @{}) {
    # Actions: cancel_at, kill_worker_at, kill_console_at  (seconds after task starts preparing/running)
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $firstRunning = $null
    $done = @{}
    while ($sw.Elapsed.TotalSeconds -lt $MaxSec) {
        Start-Sleep -Seconds 2
        $t = Invoke-Api GET "/api/tasks/$($task.task_id)"
        # sample GPU + worker RSS + external network connections every cycle
        $gpuRaw = nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits
        $gpu = [int]($gpuRaw | Select-Object -First 1)
        if ($gpu -gt $Summary.gpu_used_peak_mib) { $Summary.gpu_used_peak_mib = $gpu }
        $w = Get-FluxWorker | Select-Object -First 1
        if ($w) {
            $rss = [math]::Round($w.WorkingSet64 / 1MB)
            if ($rss -gt $Summary.worker_rss_peak_mib) { $Summary.worker_rss_peak_mib = $rss }
            foreach ($proc in @($w.ProcessId)) {
                $nc = Get-NetTCPConnection -OwningProcess $proc -ErrorAction SilentlyContinue |
                    Where-Object { $_.RemoteAddress -and $_.RemoteAddress -notmatch '^(127\.|::1|::ffff:127\.)' }
                foreach ($cc in $nc) { $Summary.external_connections += "worker PID $proc -> $($cc.RemoteAddress):$($cc.RemotePort)" }
            }
        }
        if ($t.status -in @('preparing', 'running') -and $null -eq $firstRunning) { $firstRunning = $sw.Elapsed.TotalSeconds }
        if ($null -ne $firstRunning) {
            $dur = $sw.Elapsed.TotalSeconds - $firstRunning
            if ($Actions.ContainsKey('cancel_at') -and -not $done.ContainsKey('cancel') -and $dur -ge $Actions.cancel_at) {
                Log "cancel: invoking cancel on task $($t.task_id) after ${dur}s"
                Invoke-Api POST "/api/tasks/$($t.task_id)/cancel" | Out-Null
                $done['cancel'] = $true
            }
            if ($Actions.ContainsKey('kill_worker_at') -and -not $done.ContainsKey('kill_worker') -and $dur -ge $Actions.kill_worker_at) {
                $w2 = Get-FluxWorker | Select-Object -First 1
                if ($w2) { Log "crash test: force-killing worker PID=$($w2.ProcessId)"; Stop-Process -Id $w2.ProcessId -Force }
                $done['kill_worker'] = $true
            }
            if ($Actions.ContainsKey('kill_console_at') -and -not $done.ContainsKey('kill_console') -and $dur -ge $Actions.kill_console_at) {
                Log 'restart test: stopping web console during generation'
                Stop-WebConsole
                return $t
            }
        }
        if ($t.status -in @('completed', 'failed', 'cancelled')) { return $t }
    }
    return (Invoke-Api GET "/api/tasks/$($task.task_id)")
}

function Verify-Outputs($task, $label) {
    foreach ($f in $task.output_files) {
        $path = $f.path
        if (-not (Test-Path $path)) { Add-Step "$label output exists" $false "missing: $path"; continue }
        $sizeKb = [math]::Round((Get-Item $path).Length / 1KB)
        $hash = (Get-FileHash -Algorithm SHA256 $path).Hash
        $pilOut = & $ImagePy -c "from PIL import Image; im=Image.open(r'$path'); im.load(); ex=im.getextrema(); span=min(max(c)-min(c) for c in ex) if isinstance(ex, list) else (ex[1]-ex[0]); print(im.size[0], im.size[1], im.mode, span)"
        $parts = "$pilOut".Split(' ')
        $w = [int]$parts[0]; $h = [int]$parts[1]; $span = [double]$parts[3]
        $ok = ($w -ge 16 -and $h -ge 16 -and $sizeKb -ge 10 -and $span -ge 16)
        [void]$Summary.outputs.Add([ordered]@{ task_id = $task.task_id; label = $label; path = $path; size_kb = $sizeKb; sha256 = $hash; decoded = "$w x $h $($parts[2])"; contrast_span = $span })
        Add-Step "$label valid image" $ok "$path (${sizeKb}KB, ${w}x${h}, contrast_span=$span)"
    }
}

# ---------------------------------------------------------------- Step 0: pre-flight
Log "log directory: $LogDir"
$gpuFree = [int]((nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits) | Select-Object -First 1)
$ramFreeGb = [math]::Round(((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB), 1)
Log "GPU free: ${gpuFree} MiB | RAM free: ${ramFreeGb} GB"
$weightInfo = & $VenvPy -c "import glob,os;d=glob.glob(r'$Root\models\flux-schnell\models--*\snapshots\*');fs=[os.path.join(r,f) for r,_,fl in os.walk(d[0]) for f in fl];print(len(fs), round(sum(os.stat(f).st_size for f in fs)/1e9,2))"
Log "FLUX snapshot: $weightInfo (expect: 23 files, ~33.7 GB)"
$depInfo = & $ImagePy -c "import torch,sentencepiece,google.protobuf as p;print(torch.__version__, torch.cuda.is_available(), sentencepiece.__version__, p.__version__)"
Log "worker deps: $depInfo"
$preOk = ($gpuFree -ge $GpuFreeMinMib) -and ($ramFreeGb -ge $RamFreeMinGb) -and ($weightInfo -match '^23 ')
$waitedSec = 0
if (-not $CheckOnly -and -not $preOk -and -not $Force -and ($weightInfo -match '^23 ')) {
    Log 'resources busy: to release them double-click scripts\release_local_engine.bat (stops the'
    Log 'local LLM engine: ~11GB VRAM + ~37GB RAM), then leave the local-AI chat app (Strata engine) closed.'
    Log 'the acceptance auto-starts once free (max 120 min). Do NOT reopen the chat app or send'
    Log 'messages to the local chat model while the run is going - that steals the GPU back.'
    while (-not $preOk -and $waitedSec -lt 7200) {
        Start-Sleep -Seconds 15
        $waitedSec += 15
        $gpuFree = [int]((nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits) | Select-Object -First 1)
        $ramFreeGb = [math]::Round(((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB), 1)
        $preOk = ($gpuFree -ge $GpuFreeMinMib) -and ($ramFreeGb -ge $RamFreeMinGb)
        if (-not $preOk -and ($waitedSec % 60 -eq 0)) { Log "waiting... gpu_free=${gpuFree}MiB ram_free=${ramFreeGb}GB (${waitedSec}s/7200s)" }
    }
    if ($preOk) { Log "resources free after ${waitedSec}s - starting acceptance run" }
}
Add-Step 'pre-flight (GPU/RAM free, weights)' $preOk "gpu_free_mib=$gpuFree ram_free_gb=$ramFreeGb waited_s=$waitedSec weights='$weightInfo'"
if ($CheckOnly) { $Summary.overall = if ($preOk) { 'pre-flight PASS' } else { 'pre-flight FAIL' }; Save-Summary; Stop-Transcript | Out-Null; exit $(if ($preOk) { 0 } else { 1 }) }
if (-not $preOk -and -not $Force) {
    Log 'ABORT: resources stayed busy for 60 min (or weights incomplete). Nothing was changed.'
    $Summary.overall = 'aborted: resources busy'
    Save-Summary; Stop-Transcript | Out-Null; exit 1
}

try {
    # ------------------------------------------------------- Step 1: console restart
    Stop-WebConsole
    Start-WebConsole
    $engines = Invoke-Api GET '/api/engines'
    $engText = ($engines.engines | ForEach-Object { "$($_.engine_id)=$($_.state)" }) -join ', '
    Add-Step 'console restart (HF_HUB_OFFLINE=1)' $true $engText

    # ------------------------------------------------------- Step 2: end-to-end generation
    Log 'submitting baseline image task (512x512, steps=4, seed=420707)'
    $t2 = Submit-Image 'a fluffy red panda sitting on a mossy rock in a misty bamboo forest, soft cinematic light, photorealistic, highly detailed' @{ width = 512; height = 512; steps = 4; seed = 420707 }
    $t2 = Poll-Task $t2 900
    $dur2 = [pscustomobject]@{ from = [DateTimeOffset]::Parse($t2.created_at); to = [DateTimeOffset]::Parse($t2.finished_at) }
    $genSec = if ($t2.finished_at) { [math]::Round(($dur2.to - $dur2.from).TotalSeconds, 1) } else { $null }
    Add-Step 'baseline generation completes' ($t2.status -eq 'completed') "status=$($t2.status) total_sec=$genSec err=$($t2.error_message)"
    if ($t2.status -eq 'completed') { Verify-Outputs $t2 'baseline' }

    # --------------------------------------- Step 2b: Chinese prompt end-to-end (zh encode fix)
    Log 'submitting Chinese-prompt task (1216x704 steps=4 - same shape as the failed batch)'
    $tZ = Submit-Image '一只橘猫趴在窗台上晒太阳，午后暖色阳光，写实摄影风格，毛发细节清晰，浅景深，8k，高清' @{ width = 1216; height = 704; steps = 4; seed = 20261105 }
    $tZ = Poll-Task $tZ 900
    $genZ = if ($tZ.finished_at) { [math]::Round(([DateTimeOffset]::Parse($tZ.finished_at) - [DateTimeOffset]::Parse($tZ.created_at)).TotalSeconds, 1) } else { $null }
    Add-Step 'Chinese-prompt generation completes' ($tZ.status -eq 'completed') "status=$($tZ.status) total_sec=$genZ err=$($tZ.error_message)"
    if ($tZ.status -eq 'completed') { Verify-Outputs $tZ 'chinese-prompt' }

    # ------------------------------------------------------- Step 3: cancel queued + running
    $tA = Submit-Image 'a wide panoramic mountain valley at golden hour, epic scale, photorealistic' @{ width = 1024; height = 1024; steps = 30; seed = 7 }
    Start-Sleep -Seconds 1
    $tB = Submit-Image 'this task should be cancelled while still queued' @{ width = 512; height = 512; steps = 4; seed = 1 }
    $tBc = Invoke-Api POST "/api/tasks/$($tB.task_id)/cancel"
    Add-Step 'cancel queued task' ($tBc.status -eq 'cancelled') "status=$($tBc.status) msg=$($tBc.error_message)"
    $tAc = Poll-Task $tA 600 @{ cancel_at = 25 }
    Add-Step 'cancel running task' ($tAc.status -eq 'cancelled') "status=$($tAc.status) msg=$($tAc.error_message)"
    Start-Sleep -Seconds 3
    $wGone = $null -eq (Get-FluxWorker | Select-Object -First 1)
    Add-Step 'worker terminated after running-cancel' $wGone 'flux worker process should be gone after cancel'

    # ------------------------------------------------------- Step 4: crash -> failed -> retry
    $tC = Submit-Image 'a small lighthouse on a rocky island under northern lights, photorealistic' @{ width = 512; height = 512; steps = 4; seed = 12345 }
    $tC = Poll-Task $tC 900 @{ kill_worker_at = 15 }
    Add-Step 'worker crash marks task failed' ($tC.status -eq 'failed') "status=$($tC.status) err=$($tC.error_message)"
    $tR = Invoke-Api POST "/api/tasks/$($tC.task_id)/retry"
    $tR = Poll-Task $tR 900
    Add-Step 'retry after crash completes' ($tR.status -eq 'completed') "status=$($tR.status) retry_of=$($tR.retry_of) err=$($tR.error_message)"
    if ($tR.status -eq 'completed') { Verify-Outputs $tR 'retry-after-crash' }

    # ------------------------------------------------------- Step 5: console restart recovery
    $tD = Submit-Image 'an autumn forest path covered with orange leaves, morning fog, photorealistic' @{ width = 1024; height = 1024; steps = 30; seed = 8 }
    $tD = Poll-Task $tD 600 @{ kill_console_at = 20 }
    Start-Sleep -Seconds 2
    Start-WebConsole
    $tD2 = Invoke-Api GET "/api/tasks/$($tD.task_id)"
    Add-Step 'console restart marks interrupted task failed' ($tD2.status -eq 'failed') "status=$($tD2.status) msg=$($tD2.error_message)"
    $tE = Invoke-Api POST "/api/tasks/$($tD.task_id)/retry"
    $tE = Poll-Task $tE 900
    Add-Step 'retry after console restart completes' ($tE.status -eq 'completed') "status=$($tE.status) err=$($tE.error_message)"
    if ($tE.status -eq 'completed') { Verify-Outputs $tE 'retry-after-console-restart' }

    # ------------------------------------------------------- Step 6: offline + network isolation
    $offlineOk = ($env:HF_HUB_OFFLINE -eq '1') -and ($Summary.external_connections.Count -eq 0)
    Add-Step 'offline mode + no external connections' $offlineOk "HF_HUB_OFFLINE=1; external_conns=$($Summary.external_connections -join '; ')"
    $consoleConn = Get-NetTCPConnection -OwningProcess (Get-NetTCPConnection -LocalPort 8765 -State Listen).OwningProcess -ErrorAction SilentlyContinue |
        Where-Object { $_.RemoteAddress -notmatch '^(127\.|::1|::ffff:127\.)' }
    if ($consoleConn) { $Summary.external_connections += 'console had external connections'; $offlineOk = $false }

    $failed = @($Summary.steps | Where-Object { -not $_.pass })
    $Summary.overall = if ($failed.Count -eq 0) { 'PASS' } else { "FAIL ($($failed.Count) steps)" }
    Save-Summary
    Log "OVERALL: $($Summary.overall) | gpu_used_peak=$($Summary.gpu_used_peak_mib) MiB worker_rss_peak=$($Summary.worker_rss_peak_mib) MiB"
    Log "summary: $(Join-Path $LogDir 'acceptance_summary.json')"
    Stop-Transcript | Out-Null
    exit $(if ($failed.Count -eq 0) { 0 } else { 1 })
}
catch {
    Add-Step 'fatal' $false "$_"
    $Summary.overall = 'aborted: exception'
    Save-Summary
    Log "FATAL: $_"
    Stop-Transcript | Out-Null
    exit 1
}
