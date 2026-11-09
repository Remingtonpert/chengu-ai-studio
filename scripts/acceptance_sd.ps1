# acceptance_sd.ps1 - wait until enough VRAM is free, then run the REAL SD image
# acceptance (txt2img + img2img, both must land on disk). Exit 0 only on a real pass.
param([int]$MinFreeMB = 2500, [int]$MinFreeRAMMB = 8000, [int]$MaxRounds = 40, [int]$WaitSeconds = 180)
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root '.venv-image\Scripts\python.exe'
for ($r = 1; $r -le $MaxRounds; $r++) {
    $free = [int](nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | Select-Object -First 1)
    $freeRamMB = [int]((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1KB)
    Write-Host ("acceptance_sd round {0}: free VRAM {1} MB (need {2}), free RAM {3} MB (need {4})" -f $r, $free, $MinFreeMB, $freeRamMB, $MinFreeRAMMB)
    if (($free -ge $MinFreeMB) -and ($freeRamMB -ge $MinFreeRAMMB)) {
        & $py (Join-Path $root 'scripts\acceptance_sd.py') --min-free $MinFreeMB
        exit $LASTEXITCODE
    }
    Start-Sleep -Seconds $WaitSeconds
}
Write-Host 'acceptance_sd gave up: VRAM/RAM window never opened (another program is holding GPU and system memory)'
exit 2
