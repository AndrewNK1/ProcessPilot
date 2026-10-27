<#
.SYNOPSIS
    Start ProcessPilot: the API and the web app, in one process.

.DESCRIPTION
    Starts the FastAPI backend (which also serves the built web app from
    src\processpilot\api\static), waits until it answers /health, then opens
    the app in the default browser. Ctrl+C stops it.
    Host and port come from configs\app.yaml. Node is not needed: the built
    app is committed. To change the UI, see web\README.md (npm run dev).
    Works in Windows PowerShell 5.1 and PowerShell 7.

.PARAMETER NoBrowser
    Do not open the browser.

.EXAMPLE
    .\scripts\run_app.ps1
#>
param([switch]$NoBrowser)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\python.exe"
if (-not (Test-Path $python)) { throw "Python environment not found at $python (see README: Setup)." }
if (-not (Test-Path (Join-Path $root "src\processpilot\api\static\index.html"))) {
    Write-Warning "The web app is not built; the API will run, the app will not. Run 'npm ci; npm run build' in web\."
}

$settings = & $python -c "from processpilot.config import load_config_yaml as l; c = l('app.yaml'); print(c['api']['host'], c['api']['port'])"
$apiHost, $apiPort = $settings.Trim().Split(" ")
$appUrl = "http://${apiHost}:${apiPort}/"

$logDir = Join-Path $root "outputs\app"
New-Item -ItemType Directory -Force $logDir | Out-Null
$apiLog = Join-Path $logDir "api.log"
$apiErr = Join-Path $logDir "api.err.log"

Write-Host "Starting ProcessPilot on $appUrl (log: $apiLog) ..."
$api = Start-Process -FilePath $python -ArgumentList "-m", "processpilot.api" `
    -WorkingDirectory $root -PassThru -NoNewWindow `
    -RedirectStandardOutput $apiLog -RedirectStandardError $apiErr

try {
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        if ($api.HasExited) { throw "The API stopped during start-up; see $apiErr" }
        try {
            Invoke-RestMethod -Uri "${appUrl}health" -TimeoutSec 2 | Out-Null
            $ready = $true
            break
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $ready) { throw "The API did not answer ${appUrl}health within 30 s; see $apiErr" }
    Write-Host "Ready: $appUrl  (Ctrl+C to stop)"
    if (-not $NoBrowser) { Start-Process $appUrl }
    # The launcher stays alive while the API runs: pm4py looks up its parent
    # process at import (see api/main.py), and Ctrl+C here stops both.
    Wait-Process -Id $api.Id
} finally {
    if (-not $api.HasExited) {
        Write-Host "Stopping ProcessPilot ..."
        Stop-Process -Id $api.Id -Force
    }
}
