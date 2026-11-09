# Chained acceptance waiter: SD images -> Wan2.1 video -> ACE-Step music.
# Each inner script self-waits for free resources (2h window) and exits 0 only on real pass.
# Honest by design: on failure nothing is changed; rounds are logged separately.
param([int]$MaxRounds = 48)
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$loopLog = Join-Path $root 'logs\acceptance_loop_turn40.log'
"=== chain start $(Get-Date -Format 'yyyy-MM-dd HH:mm') ===" | Tee-Object -FilePath $loopLog -Append

foreach ($step in @('sd', 'video', 'music')) {
    for ($r = 1; $r -le $MaxRounds; $r++) {
        "=== $step acceptance round $r $(Get-Date -Format 'HH:mm') ===" | Tee-Object -FilePath $loopLog -Append
        $ps1 = Join-Path $root ("scripts\acceptance_{0}.ps1" -f $step)
        & pwsh -NoProfile -ExecutionPolicy Bypass -File $ps1 *>&1 |
            Tee-Object -FilePath (Join-Path $root ("logs\acceptance_{0}_r{1}.log" -f $step, $r))
        if ($LASTEXITCODE -eq 0) {
            "$step round $r PASS" | Tee-Object -FilePath $loopLog -Append
            break
        }
        "$step round $r fail (exit $LASTEXITCODE)" | Tee-Object -FilePath $loopLog -Append
        if ($r -eq $MaxRounds) {
            "$step rounds exhausted; chain aborted" | Tee-Object -FilePath $loopLog -Append
            exit 1
        }
    }
}
"ACCEPTANCE CHAIN COMPLETE (SD + VIDEO + MUSIC) $(Get-Date -Format 'yyyy-MM-dd HH:mm')" | Tee-Object -FilePath $loopLog -Append
