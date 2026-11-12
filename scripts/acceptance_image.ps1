# acceptance_image.ps1 - wait until enough VRAM *and* system memory are free, then run the
# REAL Sana multilingual txt2img acceptance (1024-class single pass + 1080p upscale path),
# printing the measured peak VRAM for every stage. Exit 0 only on a real pass.
#
# 门槛默认按 6GB 显存档位设计（6144MB 空闲显存 + 16GB 空闲物理内存）：等待阶段只轮询
# nvidia-smi 与系统内存计数，不加载权重、不碰显卡，所以「显卡占用过高」时它只是等待，
# 不会把 OOM 当成测试结论。真正的实测在 acceptance_image.py 里，那里任何一张的实测峰值
# 超过门槛就立即停止后续档位。
param([int]$MinFreeMB = 6144, [int]$MinFreeRAMMB = 16000, [int]$MaxRounds = 40, [int]$WaitSeconds = 180)
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root '.venv-image\Scripts\python.exe'
for ($r = 1; $r -le $MaxRounds; $r++) {
    $free = [int](nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | Select-Object -First 1)
    $freeRamMB = [int]((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1KB)
    Write-Host ("acceptance_image round {0}: free VRAM {1} MB (need {2}), free RAM {3} MB (need {4})" -f $r, $free, $MinFreeMB, $freeRamMB, $MinFreeRAMMB)
    if (($free -ge $MinFreeMB) -and ($freeRamMB -ge $MinFreeRAMMB)) {
        & $py (Join-Path $root 'scripts\acceptance_image.py') --min-free $MinFreeMB --min-free-ram $MinFreeRAMMB
        exit $LASTEXITCODE
    }
    Start-Sleep -Seconds $WaitSeconds
}
Write-Host 'acceptance_image gave up: VRAM/RAM window never opened (another program is holding GPU and system memory)'
exit 2
