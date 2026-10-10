# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Gilbert Kamau
# Scheduled update: -Mode weekly (retrain) or news (prices/injuries). Logs to data\logs.
param([ValidateSet("weekly", "news")][string]$Mode = "weekly")

$root = Split-Path -Parent $PSScriptRoot
$bin = Join-Path $root ".venv\Scripts"
$logDir = Join-Path $root "data\logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir ("{0}-{1}.log" -f (Get-Date -Format "yyyyMMdd-HHmm"), $Mode)

# cmd keeps stderr as plain text (PowerShell 5.1 turns it into errors).
function Invoke-Step([string]$exe, [string]$arguments) {
    cmd /c "`"$bin\$exe`" $arguments >> `"$log`" 2>&1"
    return $LASTEXITCODE
}

Set-Location $root
if ($Mode -eq "weekly") {
    $code = Invoke-Step "fpl-sync.exe" "weekly"
} else {
    $code = Invoke-Step "fpl-sync.exe" "live"
    if ($code -eq 0) { $code = Invoke-Step "fpl-model.exe" "predict" }
}
Add-Content $log "exit code: $code"
exit $code
