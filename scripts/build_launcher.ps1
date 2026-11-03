# 编译 Strata.exe 启动器（使用 Windows 自带的 .NET Framework C# 编译器，无需联网）。
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Source = Join-Path $PSScriptRoot "launcher\StrataLauncher.cs"
$Output = Join-Path $ProjectRoot "Strata.exe"

$cscCandidates = @(
    (Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"),
    (Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe")
)
$csc = $cscCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $csc) {
    throw "未找到 .NET Framework 的 csc.exe 编译器（路径: $cscCandidates）。"
}

& $csc /nologo /target:exe /platform:anycpu /optimize+ /out:"$Output" "$Source"
if ($LASTEXITCODE -ne 0) {
    throw "启动器编译失败（退出码 $LASTEXITCODE）。"
}

Write-Host "已生成启动器: $Output"
