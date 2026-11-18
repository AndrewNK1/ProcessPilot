# Start the evaluation campaign for today's quota (run by hand or by Task Scheduler).
#
# By hand, from the project root:
#   .\scripts\eval\run_daily.ps1                      # campaign eval-2026-11-v1
#   .\scripts\eval\run_daily.ps1 -Campaign other-id
#
# Task Scheduler (optional; NOT registered by any script): create a task that runs
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "<project>\scripts\eval\run_daily.ps1"
# daily at 10:15 Cairo time. The Gemini quota resets at midnight Pacific, which
# is 10:00 Cairo in November; both zones change clocks on different dates, so
# check the reset time the runner prints (computed with zoneinfo) if you run
# the campaign near a DST change. "Start in" = the project folder.
# The runner stops by itself when the day's budget cannot cover the next run.
param([string]$Campaign = "eval-2026-11-v1")

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root
$log = Join-Path $root "outputs\eval\$Campaign\runner_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"
New-Item -ItemType Directory -Force (Split-Path -Parent $log) | Out-Null
& "$root\.venv\python.exe" "$root\scripts\eval\run_campaign.py" --campaign $Campaign *>&1 | Tee-Object -FilePath $log
exit $LASTEXITCODE
