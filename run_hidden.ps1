# Starts the dashboard + Discord bot with NO console window.
#
# This is what the Windows scheduled task runs at logon (see autostart.ps1).
# It starts supervisor.py, which starts app.py and restarts it if it dies.
#
# Output goes to logs\supervisor.log and logs\app.log, and the PID of the
# running app is written to logs\app.pid so it can be stopped again.

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# Prefer the project virtualenv, fall back to whatever python is on PATH.
$python = $null
foreach ($rel in @(".venv2\Scripts\pythonw.exe", ".venv\Scripts\pythonw.exe")) {
    $p = Join-Path $root $rel
    if (Test-Path $p) { $python = $p; break }
}
if (-not $python) {
    $cmd = Get-Command pythonw -ErrorAction SilentlyContinue
    if (-not $cmd) { $cmd = Get-Command python -ErrorAction SilentlyContinue }
    if ($cmd) { $python = $cmd.Source }
}
if (-not $python) {
    Write-Error "python not found - install Python or create .venv2 in $root"
    exit 1
}

$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# Already running? The supervisor has its own lock, so a second copy exits on
# its own - but checking here keeps the log clean.
$pidFile = Join-Path $logDir "app.pid"
if (Test-Path $pidFile) {
    $old = Get-Content $pidFile -ErrorAction SilentlyContinue
    if ($old -and (Get-Process -Id $old -ErrorAction SilentlyContinue)) {
        Write-Host "Already running (PID $old)."
        exit 0
    }
}

Start-Process -FilePath $python `
    -ArgumentList @((Join-Path $root "supervisor.py")) `
    -WorkingDirectory $root `
    -WindowStyle Hidden | Out-Null

Write-Host "Supervisor started. Dashboard: http://localhost:5000"
Write-Host "Logs: $logDir"
