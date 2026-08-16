param(
    [int]$Port = 18121,
    [switch]$NoOpen
)

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$Contract = Join-Path $ProjectRoot 'eval\diagnostic120\human\simple120-review-v1\review_contract.json'

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Project Python was not found: $Python"
}
if (-not (Test-Path -LiteralPath $Contract -PathType Leaf)) {
    throw "Review contract was not found: $Contract"
}

$Arguments = @(
    '-m', 'eval.chat02.reviewer.server',
    '--contract', $Contract,
    '--port', $Port
)
if ($NoOpen) {
    $Arguments += '--no-open'
}

Set-Location -LiteralPath $ProjectRoot
& $Python @Arguments
