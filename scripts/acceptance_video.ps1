# acceptance_video.ps1 - wait until enough VRAM is free, then run the REAL Wan2.1
# acceptance (txt2video + image2video, both mp4s must land on disk).
param([int]$MinFreeMB = 4000, [int]$MinFreeRAMMB = 16000, [int]$MaxRounds = 40, [int]$WaitSeconds = 180, [switch]$SkipI2V)
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root '.venv-video\Scripts\python.exe'
for ($r = 1; $r -le $MaxRounds; $r++) {
    $free = [int](nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | Select-Object -First 1)
    $freeRamMB = [int]((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1KB)
    Write-Host ("acceptance_video round {0}: free VRAM {1} MB (need {2}), free RAM {3} MB (need {4})" -f $r, $free, $MinFreeMB, $freeRamMB, $MinFreeRAMMB)
    if (($free -ge $MinFreeMB) -and ($freeRamMB -ge $MinFreeRAMMB)) {
        if ($SkipI2V) { & $py (Join-Path $root 'scripts\acceptance_video.py') --min-free $MinFreeMB --skip-i2v }
        else { & $py (Join-Path $root 'scripts\acceptance_video.py') --min-free $MinFreeMB }
        exit $LASTEXITCODE
    }
    Start-Sleep -Seconds $WaitSeconds
}
Write-Host 'acceptance_video gave up: VRAM/RAM window never opened (another program is holding the GPU and system memory)'
exit 2
