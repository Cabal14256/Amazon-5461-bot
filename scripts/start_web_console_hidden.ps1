[CmdletBinding()]
param(
    [string]$WebHost = '',
    [ValidateRange(0, 65535)]
    [int]$WebPort = 0
)

$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$serverScript = Join-Path $projectRoot 'scripts\run_web_console.py'
$logsDirectory = Join-Path $projectRoot 'runtime\logs'

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Project interpreter not found: $pythonPath"
}
if (-not (Test-Path -LiteralPath $serverScript -PathType Leaf)) {
    throw "Web console entry point not found: $serverScript"
}

# Do not start a second console when one was launched manually before logon.
$existingConsole = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Name -in @('python.exe', 'pythonw.exe') -and
        [string]$_.CommandLine -like '*run_web_console.py*'
    } |
    Select-Object -First 1
if ($existingConsole) {
    exit 0
}

New-Item -ItemType Directory -Force -Path $logsDirectory | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$stdoutPath = Join-Path $logsDirectory "web_console_$stamp.out.log"
$stderrPath = Join-Path $logsDirectory "web_console_$stamp.err.log"

$env:PYTHONUTF8 = '1'
$serverArguments = @("`"$serverScript`"")
if ($WebHost) {
    $serverArguments += @('--host', $WebHost)
}
if ($WebPort -gt 0) {
    $serverArguments += @('--port', [string]$WebPort)
}
$process = Start-Process `
    -FilePath $pythonPath `
    -ArgumentList $serverArguments `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath `
    -PassThru `
    -Wait

exit $process.ExitCode
