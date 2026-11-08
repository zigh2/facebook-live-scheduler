# Registers a Windows Scheduled Task that starts the scheduler at logon and restarts it if it dies.
# Run once from an elevated PowerShell:  powershell -ExecutionPolicy Bypass -File scripts\register-task-windows.ps1
# (It must run in YOUR logged-in session, because OBS needs the desktop/GPU.)
$project = Split-Path -Parent $PSScriptRoot
$bat = Join-Path $PSScriptRoot "run.bat"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$bat`"" -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "FBLiveScheduler" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Task 'FBLiveScheduler' registered. It will start at your next logon (or run it now from Task Scheduler)."
