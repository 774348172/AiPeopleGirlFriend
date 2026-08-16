param(
    [int]$Port = 18120,
    [switch]$NoOpen
)

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Project Python was not found: $Python"
}

$Arguments = @('-m', 'eval.chat02.reviewer.server', '--port', $Port)
if ($NoOpen) {
    $Arguments += '--no-open'
}

Set-Location -LiteralPath $ProjectRoot
& $Python @Arguments
