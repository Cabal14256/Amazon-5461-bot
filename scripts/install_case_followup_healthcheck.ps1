param(
    [int]$IntervalMinutes = 5
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$taskRunner = Join-Path $PSScriptRoot 'run_case_followup_healthcheck_task.ps1'
$powershellPath = (Get-Command powershell.exe -ErrorAction Stop).Source
$taskName = 'Amazon5461-CaseFollowupHealth'

if (-not (Test-Path -LiteralPath $taskRunner)) {
    throw "Case follow-up task runner not found: $taskRunner"
}

$action = New-ScheduledTaskAction `
    -Execute $powershellPath `
    -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $taskRunner) `
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
    -Description 'Runs and waits for the Amazon 5461 Case follow-up worker every five minutes without a model watchdog.' `
    -Force | Out-Null

Write-Output "installed=$taskName interval_minutes=$IntervalMinutes"
