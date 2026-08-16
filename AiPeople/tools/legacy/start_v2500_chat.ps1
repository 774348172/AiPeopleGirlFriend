$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
$proxyScript = Join-Path $projectRoot "tools\v2500_chat_proxy.py"
$stdoutLog = Join-Path $projectRoot "local_runtime\v2500_chat_proxy.stdout.log"
$stderrLog = Join-Path $projectRoot "local_runtime\v2500_chat_proxy.stderr.log"

if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw "Project Python not found: $pythonExe"
}

$existing = Get-NetTCPConnection -State Listen -LocalPort 18082 -ErrorAction SilentlyContinue
if (-not $existing) {
    Start-Process `
        -FilePath $pythonExe `
        -ArgumentList @($proxyScript, "--port", "18082", "--backend-port", "18081") `
        -WorkingDirectory $projectRoot `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -WindowStyle Hidden
}

Write-Output "http://127.0.0.1:18082/?new_chat=true"
