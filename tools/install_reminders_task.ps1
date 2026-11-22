param(
    [string]$TaskName = "PVL-Reminders",
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$ScriptPath = (Resolve-Path (Join-Path $PSScriptRoot "..\напоминания.py")).Path
$WorkDir = Split-Path -Parent $ScriptPath

if (-not $PythonExe) {
    $python = Get-Command python -ErrorAction Stop
    $candidate = Join-Path (Split-Path -Parent $python.Source) "pythonw.exe"
    if (Test-Path $candidate) {
        $PythonExe = $candidate
    } else {
        $PythonExe = $python.Source
    }
}

$action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument ('"{0}"' -f $ScriptPath) `
    -WorkingDirectory $WorkDir

$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$settings = New-ScheduledTaskSettingsSet `
    -Hidden `
    -WakeToRun `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "PVL: Telegram reminders for calls every 5 minutes" `
    -Force | Out-Null

Write-Host "Task registered: $TaskName"
Write-Host "Python: $PythonExe"
Write-Host "Script: $ScriptPath"
Write-Host "Schedule: every 5 minutes"