[CmdletBinding()]
param(
    [string]$OutputDirectory,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $PSScriptRoot "bin"
}

$compilerCandidates = @(
    (Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"),
    (Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe")
)
$compiler = $compilerCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $compiler) {
    throw ".NET Framework 4.x C# compiler was not found. Enable .NET Framework 4.8 on Windows."
}

$sourceRoot = $PSScriptRoot
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $resolvedOutput | Out-Null

$launcherExe = Join-Path $resolvedOutput "AiPeople.exe"
$launcherSources = @(
    (Join-Path $sourceRoot "LauncherCore.cs"),
    (Join-Path $sourceRoot "JobObject.cs"),
    (Join-Path $sourceRoot "Program.cs")
)

& $compiler `
    /nologo `
    /target:winexe `
    /platform:anycpu `
    /optimize+ `
    /warn:4 `
    "/out:$launcherExe" `
    /reference:System.dll `
    /reference:System.Core.dll `
    /reference:System.Drawing.dll `
    /reference:System.Windows.Forms.dll `
    /reference:System.Web.Extensions.dll `
    $launcherSources
if ($LASTEXITCODE -ne 0) {
    throw "Launcher compilation failed with exit code $LASTEXITCODE."
}

Write-Host "Built launcher: $launcherExe"

if (-not $SkipTests) {
    $testExe = Join-Path $resolvedOutput "LauncherTests.exe"
    $testSources = @(
        (Join-Path $sourceRoot "LauncherCore.cs"),
        (Join-Path $sourceRoot "LauncherTests.cs")
    )
    & $compiler `
        /nologo `
        /target:exe `
        /platform:anycpu `
        /optimize+ `
        /warn:4 `
        /main:AiPeople.PortableLauncher.LauncherTests `
        "/out:$testExe" `
        /reference:System.dll `
        /reference:System.Core.dll `
        /reference:System.Web.Extensions.dll `
        $testSources
    if ($LASTEXITCODE -ne 0) {
        throw "Launcher test compilation failed with exit code $LASTEXITCODE."
    }

    & $testExe $launcherExe
    if ($LASTEXITCODE -ne 0) {
        throw "Launcher tests failed with exit code $LASTEXITCODE."
    }
}
