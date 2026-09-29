param(
    [string]$PythonExe = $env:AIPEOPLE_PYTHON,
    [ValidateRange(1, 65535)][int]$Port = 8767,
    [string]$DatabasePath,
    [switch]$CheckOnly
)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$runtimePython = $PythonExe
if (-not $runtimePython) {
    $projectPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $projectPython -PathType Leaf) {
        $runtimePython = $projectPython
    } else {
        $runtimePython = (Get-Command python.exe -ErrorAction Stop).Source
    }
}
if (-not (Test-Path -LiteralPath $runtimePython -PathType Leaf)) {
    throw "Python not found. Set AIPEOPLE_PYTHON or pass -PythonExe with an absolute interpreter path."
}
Push-Location $projectRoot
try {
    # Check the selected environment without installing or upgrading packages.
    'import sys, importlib.metadata as m; required = ("torch", "transformers", "unsloth", "peft", "bitsandbytes", "fastapi", "uvicorn", "jsonschema"); print("Python:", sys.executable); print({name: m.version(name) for name in required})' | & $runtimePython -X utf8 -
    if ($LASTEXITCODE -ne 0) { throw "Inference dependencies are missing in the selected Python environment." }
    if ($CheckOnly) { return }
    $serverArgs = @('-X', 'utf8', '-u', 'tools\baiweixi_chat_app.py', '--port', "$Port", '--model-profile', 'primary')
    if ($DatabasePath) { $serverArgs += @('--database-path', $DatabasePath) }
    & $runtimePython @serverArgs
    if ($LASTEXITCODE -ne 0) { throw "AiPeople backend exited with code $LASTEXITCODE. See the startup log above." }
} finally {
    Pop-Location
}
