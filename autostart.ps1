<#
.SYNOPSIS
    Install / remove / inspect the Windows "run at startup" entry for this app.

.DESCRIPTION
    The bot is started by a scheduled task at every logon. The task runs
    supervisor.py, which keeps the dashboard (and the Discord bot) alive and
    restarts them if they ever die.

    Everything is hidden - no console window, no popups.

.PARAMETER Action
    Install (default) | Uninstall | Status | Start | Stop

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File autostart.ps1 -Action Install
#>
param(
    [ValidateSet("Install", "Uninstall", "Status", "Start", "Stop")]
    [string]$Action = "Install"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$taskName = "Discord AI Bot"
$logDir = Join-Path $root "logs"
$pidFile = Join-Path $logDir "app.pid"
$startupFile = Join-Path ([Environment]::GetFolderPath("Startup")) "Discord-Bot-startup.bat"

function Get-Pythonw {
    # The interpreter that has the dependencies. The old scripts looked for
    # .venv (which did not exist) and fell back to the system Python, which
    # has no discord module - that is why the app used to die at startup.
    foreach ($rel in @(".venv2\Scripts\pythonw.exe", ".venv\Scripts\pythonw.exe")) {
        $p = Join-Path $root $rel
        if (Test-Path $p) { return $p }
    }
    return $null
}

switch ($Action) {
    "Install" {
        Write-Host "Installing auto-start for $root"
        & (Join-Path $root "setup-startup.bat")
        break
    }

    "Uninstall" {
        $removed = $false
        schtasks /delete /tn $taskName /f 2>&1 | Out-Null
        schtasks /delete /tn "DiscordSelfbot" /f 2>&1 | Out-Null
        Write-Host "Scheduled task '$taskName' removed."
        $removed = $true

        if (Test-Path $startupFile) {
            Remove-Item $startupFile -Force
            Write-Host "Startup shortcut removed."
        }

        # Stop whatever is still running.
        & (Join-Path $root "autostart.ps1") -Action Stop 2>&1 | Out-Null
        break
    }

    "Start" {
        $py = Get-Pythonw
        if (-not $py) {
            Write-Error "No virtualenv found - create .venv2 first."
            break
        }
        Start-Process -FilePath $py -ArgumentList @((Join-Path $root "supervisor.py")) `
            -WorkingDirectory $root -WindowStyle Hidden
        Write-Host "Supervisor started. Dashboard: http://localhost:5000"
        break
    }

    "Stop" {
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if ($task) {
            Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        }
        # Kill the supervisor and the app it started.
        Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -like "*$root*" -and ($_.CommandLine -like "*supervisor.py*" -or $_.CommandLine -like "*app.py*") } |
            ForEach-Object {
                Write-Host "Stopping PID $($_.ProcessId) ($($_.CommandLine))"
                Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            }
        if (Test-Path $pidFile) { Remove-Item $pidFile -Force -ErrorAction SilentlyContinue }
        Write-Host "Stopped."
        break
    }

    "Status" {
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if (-not $task) { $task = Get-ScheduledTask -TaskName "DiscordSelfbot" -ErrorAction SilentlyContinue }
        if ($task) {
            $info = Get-ScheduledTaskInfo -TaskName $task.TaskName
            Write-Host "Scheduled task '$($task.TaskName)': state=$($task.State) lastRun=$($info.LastRunTime) lastResult=$($info.LastTaskResult)"
        } else {
            Write-Host "No scheduled task registered. Run: setup-startup.bat"
        }
        if (Test-Path $startupFile) { Write-Host "Startup file present: $startupFile" }

        $procs = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -like "*$root*" }
        if ($procs) {
            foreach ($p in $procs) { Write-Host "Running: PID $($p.ProcessId) - $($p.CommandLine)" }
        } else {
            Write-Host "Nothing from this folder is running."
        }

        $supLog = Join-Path $logDir "supervisor.log"
        if (Test-Path $supLog) {
            Write-Host ""
            Write-Host "Last lines of supervisor.log:"
            Get-Content $supLog -Tail 10 | ForEach-Object { Write-Host "  $_" }
        }
        break
    }
}
