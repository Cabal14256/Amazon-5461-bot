$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$workerScript = Join-Path $PSScriptRoot 'run_case_followups.py'
$logsDir = Join-Path $projectRoot 'runtime\logs'

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Project Python not found: $pythonPath"
}

New-Item -ItemType Directory -Path $logsDir -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$stdoutPath = Join-Path $logsDir "case_followup_healthcheck_$stamp.out.log"
$stderrPath = Join-Path $logsDir "case_followup_healthcheck_$stamp.err.log"
$workerArguments = '"{0}" --watch' -f $workerScript

# Keep this scheduled-task process alive until the worker exits. Windows Task
# Scheduler can terminate a detached child when a short-lived launcher ends.
$worker = Start-Process `
    -FilePath $pythonPath `
    -ArgumentList $workerArguments `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath `
    -Wait `
    -PassThru

exit $worker.ExitCode
