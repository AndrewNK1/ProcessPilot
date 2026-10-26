<#
.SYNOPSIS
    Start the ProcessPilot API and the Streamlit app together.

.DESCRIPTION
    Starts the FastAPI backend in the background (log: outputs\app\api.log),
    waits until it answers /health, then runs Streamlit in the foreground.
    Ctrl+C stops Streamlit; the API is stopped with it.
    Host, port and upload limit come from configs\app.yaml.
    Works in Windows PowerShell 5.1 and PowerShell 7.

.EXAMPLE
    .\scripts\run_app.ps1
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\python.exe"
if (-not (Test-Path $python)) { throw "Python environment not found at $python (see README: Setup)." }

$settings = & $python -c "from processpilot.config import load_config_yaml as l; c = l('app.yaml'); print(c['api']['host'], c['api']['port'], c['api']['max_upload_mb'])"
$apiHost, $apiPort, $maxUploadMb = $settings.Trim().Split(" ")
$healthUrl = "http://${apiHost}:${apiPort}/health"

$logDir = Join-Path $root "outputs\app"
New-Item -ItemType Directory -Force $logDir | Out-Null
$apiLog = Join-Path $logDir "api.log"
$apiErr = Join-Path $logDir "api.err.log"

Write-Host "Starting the API on $apiHost`:$apiPort (log: $apiLog) ..."
$api = Start-Process -FilePath $python -ArgumentList "-m", "processpilot.api" `
    -WorkingDirectory $root -PassThru -NoNewWindow `
    -RedirectStandardOutput $apiLog -RedirectStandardError $apiErr

try {
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        if ($api.HasExited) { throw "The API stopped during start-up; see $apiErr" }
        try {
            Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2 | Out-Null
            $ready = $true
            break
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $ready) { throw "The API did not answer $healthUrl within 30 s; see $apiErr" }
    Write-Host "API ready. Starting the app ..."
    & $python -m streamlit run (Join-Path $root "src\processpilot\app\streamlit_app.py") `
        --server.maxUploadSize $maxUploadMb --browser.gatherUsageStats false
} finally {
    if (-not $api.HasExited) {
        Write-Host "Stopping the API ..."
        Stop-Process -Id $api.Id -Force
    }
}
