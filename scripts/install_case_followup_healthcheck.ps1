param(
    [int]$IntervalMinutes = 5
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
$healthScript = Join-Path $PSScriptRoot 'healthcheck_case_followup_worker.py'
$taskName = 'Amazon5461-CaseFollowupHealth'

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Windowless project Python not found: $pythonPath"
}

$action = New-ScheduledTaskAction `
    -Execute $pythonPath `
    -Argument ('"{0}"' -f $healthScript) `
    -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes ([Math]::Max(1, $IntervalMinutes)))
$settings = New-ScheduledTaskSettingsSet `
    -Hidden `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description 'Keeps the Amazon 5461 Case follow-up worker available without a model watchdog.' `
    -Force | Out-Null

Write-Output "installed=$taskName interval_minutes=$IntervalMinutes"
