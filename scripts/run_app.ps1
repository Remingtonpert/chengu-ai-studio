$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw "项目虚拟环境不存在。请先运行: py -3.11 -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
}

Push-Location $ProjectRoot
try {
    & $Python -m app.bootstrap
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}