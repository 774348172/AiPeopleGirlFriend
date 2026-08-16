$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "缺少 .venv Python：$python"
}

Set-Location $projectRoot
& $python "tools\baiweixi_chat_app.py" --port 8767

