# Default is read-only. Use -Apply after reviewing the candidate list.
[CmdletBinding()]
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [ValidateRange(1, 3650)][int]$OlderThanDays = 7,
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $ProjectRoot).Path.TrimEnd('\', '/')
if (!(Test-Path -LiteralPath (Join-Path $root 'AGENTS.md')) -or
    !(Test-Path -LiteralPath (Join-Path $root '.git'))) {
    throw 'Expected the project root containing AGENTS.md and .git.'
}
$prefix = $root + [IO.Path]::DirectorySeparatorChar
if ((Get-Item -LiteralPath $root -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw 'Linked project roots are not supported.'
}
$cutoff = (Get-Date).ToUniversalTime().AddDays(-$OlderThanDays)
$tracked = @(& git -C $root -c core.quotepath=false ls-files)
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect tracked files; refusing cleanup.' }

function Assert-SafePath([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    if (!$full.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Outside project: $full"
    }
    $cursor = $full
    while ($cursor.Length -ge $root.Length) {
        $item = Get-Item -LiteralPath $cursor -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Linked path excluded: $cursor"
        }
        if ($cursor -eq $root) { break }
        $cursor = Split-Path -Parent $cursor
    }
    return $full
}

# Walk explicitly so directory junctions are never traversed, even for inventory.
function Get-SafeTree([string]$Path) {
    $queue = [Collections.Generic.Queue[string]]::new()
    $queue.Enqueue($Path)
    while ($queue.Count) {
        $item = Get-Item -LiteralPath $queue.Dequeue() -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Linked subtree excluded: $($item.FullName)"
        }
        $item
        if ($item.PSIsContainer) {
            foreach ($child in Get-ChildItem -LiteralPath $item.FullName -Force) {
                $queue.Enqueue($child.FullName)
            }
        }
    }
}

$candidates = [Collections.Generic.List[string]]::new()
$queue = [Collections.Generic.Queue[string]]::new()
$queue.Enqueue($root)
$excluded = @('.git', 'node_modules', '.venv', 'venv', '.runtime', '.next')
while ($queue.Count) {
    foreach ($dir in Get-ChildItem -LiteralPath $queue.Dequeue() -Directory -Force) {
        if ($dir.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
        $relative = $dir.FullName.Substring($prefix.Length).Replace('\', '/')
        if ($relative -eq 'services/api-server/data/runtime') { continue }
        if ($dir.Name -in @('__pycache__', '.pytest_cache', '.turbo') -or
            ($relative -like 'services/api-server/.pytest*') -or
            ($relative -in @('apps/web/test-results', 'apps/web/playwright-report'))) {
            $candidates.Add($dir.FullName)
        } elseif ($dir.Name -notin $excluded) {
            $queue.Enqueue($dir.FullName)
        }
    }
}
# Deliberately exclude uploads, baselines, backups, logs, .env and dependencies.
$temporaryTests = Join-Path $root '.runtime/pytest-tmp'
if (Test-Path -LiteralPath $temporaryTests) { $candidates.Add($temporaryTests) }
# Historical pytest --basetemp runs also used the API working directory.
# Keep this an exact allowlist; other .runtime contents can be business evidence.
foreach ($name in @(
    'pytest-canonical-full-3', 'pytest-canonical-migration',
    'pytest-p0-api-e2e', 'pytest-p0-api-focused', 'pytest-p0-maker-checker',
    'pytest-p0-regression', 'pytest-pdf-api-orchestration',
    'pytest-pdf-regression', 'pytest-tmp'
)) {
    $temporaryTests = Join-Path $root "services/api-server/.runtime/$name"
    if (Test-Path -LiteralPath $temporaryTests) { $candidates.Add($temporaryTests) }
}
foreach ($location in @('.runtime', 'services/api-server/data/runtime')) {
    $path = Join-Path $root $location
    if (!(Test-Path -LiteralPath $path)) { continue }
    [void](Assert-SafePath $path)
    foreach ($file in Get-ChildItem -LiteralPath $path -File -Force) {
        if (($location -eq '.runtime' -and $file.Name -match '^(failed-four-regression|final-regression-[0-9]+|full-api-regression(-final)?|full-regression-[0-9]+(-rerun)?|rbac-reviewer(-valid)?-repro|regression-approval-status)\.sqlite3$') -or
            ($location -ne '.runtime' -and $file.Name -match '^pytest-orch-(approve|tail)-0718\.sqlite3$')) {
            # A WAL/SHM/journal companion may indicate an active connection.
            if (@('-wal', '-shm', '-journal') | Where-Object { Test-Path -LiteralPath ($file.FullName + $_) }) { continue }
            $candidates.Add($file.FullName)
        }
    }
}

$results = @(
    foreach ($path in $candidates) {
        try {
            $full = Assert-SafePath $path
            $relative = $full.Substring($prefix.Length).Replace('\', '/')
            if ($tracked | Where-Object { $_ -eq $relative -or $_.StartsWith($relative + '/', [StringComparison]::OrdinalIgnoreCase) }) {
                throw 'Contains tracked files'
            }
            $tree = @(Get-SafeTree $full)
            if ($tree | Where-Object { $_.LastWriteTimeUtc -gt $cutoff }) { continue }
            $files = @($tree | Where-Object { !$_.PSIsContainer })
            # A historical test directory can itself contain an open SQLite DB.
            if ($files | Where-Object { $_.Name -match '-(wal|shm|journal)$' }) { continue }
            $bytes = [long](($files | Measure-Object Length -Sum).Sum)
            if ($Apply) {
                # Revalidate the absolute target immediately before recursive removal.
                [void](Assert-SafePath $full)
                Remove-Item -LiteralPath $full -Recurse -Force
            }
            [pscustomobject]@{Path=$relative; Files=$files.Count; Bytes=$bytes; Action=$(if ($Apply) {'Deleted'} else {'Candidate'})}
        } catch {
            Write-Warning "Skipped $path : $($_.Exception.Message)"
        }
    }
)
ConvertTo-Json -InputObject @($results) -Depth 3
Write-Host ("{0}: {1} paths, {2} files, {3:N2} MiB" -f $(if ($Apply) {'Deleted'} else {'Dry run'}), $results.Count, [long](($results | Measure-Object Files -Sum).Sum), (($results | Measure-Object Bytes -Sum).Sum / 1MB))
