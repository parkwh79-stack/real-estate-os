[CmdletBinding()]
param([switch]$ApiOnly, [switch]$WebOnly)
$ErrorActionPreference = 'Stop'
if ($ApiOnly -and $WebOnly) { throw 'Choose at most one of ApiOnly/WebOnly.' }
$root = Split-Path -Parent $PSScriptRoot
$api = Join-Path $root 'services/api-server'
$web = Join-Path $root 'apps/web'
$python = Join-Path $root '.runtime/api-venv/Scripts/python.exe'
$next = Join-Path $root 'node_modules/next/dist/bin/next'
$node = (Get-Command node -ErrorAction Stop).Source
$logs = Join-Path $root '.runtime/server-logs'
[void](New-Item -ItemType Directory -Path $logs -Force)
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
function Is-Listening([int]$Port) {
    return @([Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() |
        Where-Object Port -EQ $Port).Count -gt 0
}
if (!$WebOnly) {
    if (!(Test-Path -LiteralPath $python)) { throw 'API virtual environment is missing.' }
    if (Is-Listening 8000) { Write-Host 'API port 8000 is already in use; existing process preserved.' }
    else {
        $process = Start-Process -FilePath $python -ArgumentList '-m uvicorn main:app --host 127.0.0.1 --port 8000' -WorkingDirectory $api -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logs "api-$stamp.out.log") -RedirectStandardError (Join-Path $logs "api-$stamp.err.log")
        Write-Host "API launched PID=$($process.Id). Check /health/live and /health/ready before using."
    }
}
if (!$ApiOnly) {
    if (!(Test-Path -LiteralPath $next)) { throw 'Web dependencies are missing; run npm ci first.' }
    if (Is-Listening 3000) { Write-Host 'Web port 3000 is already in use; existing process preserved.' }
    else {
        $process = Start-Process -FilePath $node -ArgumentList "`"$next`" dev --hostname 127.0.0.1 --port 3000 --webpack" -WorkingDirectory $web -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logs "web-$stamp.out.log") -RedirectStandardError (Join-Path $logs "web-$stamp.err.log")
        Write-Host "Web launched PID=$($process.Id). URL: http://127.0.0.1:3000"
    }
}
Write-Host "Log directory: $logs"
