# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Gilbert Kamau
# Scheduled FPL update. Logs go to data\logs (git-ignored).
#   -Mode weekly : refresh data, retrain the model, refresh predictions (run after each gameweek)
#   -Mode news   : refresh prices/injury news and predictions only (run before each deadline)
param([ValidateSet("weekly", "news")][string]$Mode = "weekly")

$root = Split-Path -Parent $PSScriptRoot
$bin = Join-Path $root ".venv\Scripts"
$logDir = Join-Path $root "data\logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir ("{0}-{1}.log" -f (Get-Date -Format "yyyyMMdd-HHmm"), $Mode)

# Run through cmd so the tools' stderr logging goes to the file as plain text
# (Windows PowerShell 5.1 would turn each stderr line into an error record).
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
