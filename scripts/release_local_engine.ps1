# release_local_engine.ps1 - free GPU/RAM for image/video/music acceptance runs by stopping the
# LOCAL LLM engine components ONLY (strata.exe / strata-vision.exe under
# %LOCALAPPDATA%\Strata\components\). It never touches the Strata control app
# itself, the project's web console, or any python processes.
$ErrorActionPreference = 'Continue'
$roots = Join-Path $env:LOCALAPPDATA 'Strata\components'
Write-Host '== release_local_engine: freeing GPU/RAM for image/video/music acceptance =='
$targets = @(Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -and $_.ExecutablePath.StartsWith($roots, [StringComparison]::OrdinalIgnoreCase) })
if ($targets.Count -eq 0) {
    Write-Host 'no local engine processes found - already released'
} else {
    foreach ($t in $targets) {
        Write-Host ("stopping PID {0} ({1})" -f $t.ProcessId, $t.Name)
        Stop-Process -Id $t.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 3
}
Write-Host '== GPU state after release =='
nvidia-smi --query-gpu=memory.free,memory.used --format=csv
Write-Host ''
Write-Host 'IMPORTANT: do NOT open or use the Strata local-AI chat app again for ~40 minutes -'
Write-Host 'that restarts the engine and steals the GPU back. After release, re-run the'
Write-Host 'acceptance / generation task you skipped - video and music need the freed GPU.'
Read-Host 'Press Enter to close this window'
