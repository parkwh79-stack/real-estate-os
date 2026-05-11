$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$web = Join-Path $repo "apps\web"
$node = "C:\Program Files\nodejs\node.exe"
$next = Join-Path $repo "node_modules\next\dist\bin\next"
$log = Join-Path $web "next-dev.log"

Set-Location -LiteralPath $web
& $node $next dev --port 3000 --hostname 127.0.0.1 *> $log
