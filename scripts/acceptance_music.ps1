param(
    [string]$Base = 'http://127.0.0.1:8765',
    [int]$DurationSec = 120,
    [int]$MaxWaitMin = 120,
    [int]$RunTimeoutMin = 90
)
# ACE-Step 1.5 end-to-end acceptance: submit a Chinese-vocal music task through the
# running web console, wait for the real 48kHz wav, verify it honestly.
# ASCII-only body; Chinese lyrics live in music_acceptance_lyrics.txt (UTF-8).
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$VenvPy = Join-Path $Root '.venv\Scripts\python.exe'
$LyricsFile = Join-Path $PSScriptRoot 'music_acceptance_lyrics.txt'
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$LogDir = Join-Path $Root "logs\music_acceptance_$stamp"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
Start-Transcript -Path (Join-Path $LogDir 'music_acceptance.log') | Out-Null
$Summary = [ordered]@{ overall = 'running'; started = (Get-Date -Format 's'); steps = [System.Collections.ArrayList]::new() }
function Save-Summary {
    $Summary | ConvertTo-Json -Depth 8 | Set-Content -Path (Join-Path $LogDir 'music_acceptance_summary.json') -Encoding UTF8
}
function Log($m) { Write-Host "[music-acceptance] $m" }
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
function Get-GpuFree {
    [int]((nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits) | Select-Object -First 1)
}
function Get-RamFreeGb {
    [math]::Round(((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB), 1)
}

# Step 1: console alive and music engine registered
try {
    $health = Invoke-Api GET '/api/health'
    $engines = Invoke-Api GET '/api/engines'
    $music = $engines.engines | Where-Object { $_.engine_id -eq 'ace-step-15' }
    $ok1 = ($health.status -eq 'ok') -and $null -ne $music
    Add-Step 'web console + music engine registered' $ok1 "health=$($health.status) ace-step-15=$(if ($music) { $music.state } else { 'NOT REGISTERED' })"
} catch {
    Add-Step 'web console + music engine registered' $false "error: $_"
    $Summary.overall = 'FAIL'; Save-Summary; Stop-Transcript | Out-Null; exit 1
}
if ($Summary.steps[-1].pass -ne $true) { $Summary.overall = 'FAIL'; Save-Summary; Stop-Transcript | Out-Null; exit 1 }

# Step 2: wait for VRAM/RAM window (ACE-Step needs >=12.3GB free VRAM, LLM engine must be released)
$gpuFree = Get-GpuFree; $ramFree = Get-RamFreeGb
$preOk = ($gpuFree -ge 12300) -and ($ramFree -ge 36)
$waited = 0
Log "GPU free: ${gpuFree} MiB | RAM free: ${ramFree} GB"
if (-not $preOk) {
    Log 'resources busy: double-click scripts\release_local_engine.bat first, then leave the'
    Log 'local chat app closed; this acceptance auto-starts once free (max maxWaitMin).'
    $maxSec = $MaxWaitMin * 60
    while (-not $preOk -and $waited -lt $maxSec) {
        Start-Sleep -Seconds 15; $waited += 15
        $gpuFree = Get-GpuFree; $ramFree = Get-RamFreeGb
        $preOk = ($gpuFree -ge 12300) -and ($ramFree -ge 36)
        if (-not $preOk -and ($waited % 60 -eq 0)) { Log "waiting... gpu_free=${gpuFree}MiB ram_free=${ramFree}GB (${waited}s/${maxSec}s)" }
    }
}
if ($preOk) { Log "resources free after ${waited}s - submitting music task" }
Add-Step 'pre-flight (>=12.3GB VRAM, >=36GB RAM free)' $preOk "gpu_free_mib=$gpuFree ram_free_gb=$ramFree waited_s=$waited"
if (-not $preOk) { $Summary.overall = 'FAIL (resources never freed)'; Save-Summary; Stop-Transcript | Out-Null; exit 1 }

# Step 3: submit Chinese-vocal task (lyrics from UTF-8 file; English style tags per ACE-Step guidance)
$lyrics = (Get-Content -Raw -Encoding UTF8 $LyricsFile).Trim()
Log "lyrics loaded: $($lyrics.Length) chars (limit 4096)"
$task = Invoke-Api POST '/api/tasks' @{
    task_type  = 'music'
    engine_id  = 'ace-step-15'
    prompt     = 'warm chinese folk ballad, guzheng and bamboo flute, soft female vocals, nostalgic, gentle build'
    parameters = @{ duration = $DurationSec; lyrics = $lyrics; vocal_language = 'zh'; instrumental = $false }
}
Add-Step 'submit music task' ($null -ne $task.task_id) "task_id=$($task.task_id) status=$($task.status) duration=${DurationSec}s"
if ($null -eq $task.task_id) { $Summary.overall = 'FAIL'; Save-Summary; Stop-Transcript | Out-Null; exit 1 }

# Step 4: poll to terminal state (model cold start loads ~10GB weights; be generous)
$deadline = (Get-Date).AddMinutes($RunTimeoutMin)
$t = $task
while ($t.status -in @('queued', 'preparing', 'running') -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 20
    $t = Invoke-Api GET "/api/tasks/$($task.task_id)"
    Log ("poll: status={0} elapsed keep going" -f $t.status)
}
$finished = $t.status -in @('completed', 'failed', 'cancelled')
Add-Step 'task reaches terminal state' $finished "status=$($t.status) error=$($t.error_message)"
if ($t.status -ne 'completed') { $Summary.overall = 'FAIL'; Save-Summary; Stop-Transcript | Out-Null; exit 1 }

# Step 5: verify REAL 48kHz wav files exist with honest duration
$outputs = @($t.output_files)
$ok5 = $outputs.Count -ge 1
foreach ($f in $outputs) {
    if (-not (Test-Path -LiteralPath $f)) { $ok5 = $false; Log "missing output: $f"; continue }
    $sizeMb = [math]::Round((Get-Item -LiteralPath $f).Length / 1MB, 2)
    $probe = & $VenvPy -c "import wave,sys; w=wave.open(sys.argv[1]); print(w.getframerate(), round(w.getnframes()/w.getframerate(),2), w.getnchannels(), w.getsampwidth()*8)" $f
    $parts = "$probe".Split(' ')
    $rate = 0; $dur = 0.0
    if ($parts.Count -ge 2 -and [int]::TryParse($parts[0], [ref]$rate) -and [double]::TryParse($parts[1], [ref]$dur)) {
        $bitDepth = if ($parts.Count -ge 4) { $parts[3] } else { '?' }
        Log "wav OK: $f rate=${rate}Hz duration=${dur}s channels=$($parts[2]) bits=$bitDepth size=${sizeMb}MB"
        if ($rate -ne 48000 -or $dur -lt 5 -or $dur -gt ($DurationSec + 15) -or $sizeMb -lt 1) { $ok5 = $false }
    } else { $ok5 = $false; Log "wav probe failed: $f raw='$probe'" }
}
Add-Step 'real 48kHz wav verified (size/rate/duration)' $ok5 ($outputs -join '; ')

$Summary.overall = $(if ($ok5) { 'OVERALL: PASS' } else { 'FAIL' })
Save-Summary
Log $Summary.overall
Stop-Transcript | Out-Null
exit $(if ($ok5) { 0 } else { 1 })
