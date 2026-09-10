param(
    [ValidateSet('Migrate', 'Preview', 'Apply', 'Smoke')][string]$Mode = 'Preview',
    [string]$LiveRoot,
    [string]$Since,
    [string]$Until,
    [string]$After,
    [string]$WalkId,
    [ValidateRange(1, 10)][int]$Limit = 1,
    [string]$ExpectedPlan,
    [string]$BackupDirectory = 'C:/deploy/daengs/db-backups'
)
$ErrorActionPreference = 'Stop'
$sourceRoot = Split-Path $PSScriptRoot -Parent
if ([string]::IsNullOrWhiteSpace($LiveRoot)) { $LiveRoot = $sourceRoot }

function Invoke-Docker([string[]]$Arguments) {
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) { throw ('Docker operation failed: ' + $Arguments[0]) }
}

if ($Mode -eq 'Migrate') {
    # Copy only the reviewed additive migration; never check out live backend source.
    $stamp = [guid]::NewGuid().ToString('N')
    $backup = '/tmp/walk-context-backfill-' + $stamp + '.dump'
    $directory = $BackupDirectory
    [IO.Directory]::CreateDirectory($directory) | Out-Null
    try {
        $dumpCommand = 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f ' + $backup
        Invoke-Docker -Arguments @('exec', 'pgvector', 'sh', '-c', $dumpCommand)
        $backupFile = Join-Path $directory ('walk-context-backfill-' + $stamp + '.dump')
        Invoke-Docker -Arguments @('cp', ('pgvector:' + $backup), $backupFile)
        if ((Get-Item -LiteralPath $backupFile).Length -lt 1000) { throw 'Backup is unexpectedly small' }
        $sql = '/tmp/walk-context-backfill-' + $stamp + '.sql'
        $verify = '/tmp/walk-context-backfill-verify-' + $stamp + '.sql'
        Invoke-Docker -Arguments @('cp', (Join-Path $sourceRoot 'db/migrations/2026-09-10_walk_context_recollection.sql'), ('pgvector:' + $sql))
        Invoke-Docker -Arguments @('cp', (Join-Path $sourceRoot 'db/migrations/verify_2026-09-10_walk_context_recollection.sql'), ('pgvector:' + $verify))
        $command = 'exec psql -X -v ON_ERROR_STOP=1 --single-transaction -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f ' + $sql + ' -f ' + $verify
        Invoke-Docker -Arguments @('exec', 'pgvector', 'sh', '-c', $command)
        Write-Host ('Backfill schema verified; backup: ' + $backupFile)
    } finally {
        & docker exec pgvector rm -f $backup ('/tmp/walk-context-backfill-' + $stamp + '.sql') ('/tmp/walk-context-backfill-verify-' + $stamp + '.sql')
    }
    return
}

$liveHead = & git -C $LiveRoot rev-parse HEAD
if ($LASTEXITCODE -ne 0) { throw 'Live checkout is missing' }
$sourceHead = & git -C $sourceRoot rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $liveHead -ne $sourceHead) { throw 'Deploy this exact commit before collecting' }
& git -C $LiveRoot diff --quiet HEAD -- backend/src tools/walk_runtime_smoke.py
if ($LASTEXITCODE -ne 0) { throw 'Live collection source has local changes' }
Write-Host ('Tested server source: ' + $liveHead)

if ($Mode -eq 'Smoke') {
    $target = '/tmp/walk-backfill-smoke-' + [guid]::NewGuid().ToString('N') + '.py'
    try {
        Invoke-Docker -Arguments @('cp', (Join-Path $sourceRoot 'tools/walk_runtime_smoke.py'), ('daengs-backend:' + $target))
        Invoke-Docker -Arguments @('exec', '-w', '/app', 'daengs-backend', 'uv', 'run', '--no-sync', 'python', $target, '--execute', '--backfill')
    } finally { & docker exec daengs-backend rm -f $target }
    return
}

$arguments = @('exec', '-w', '/app', 'daengs-backend', 'uv', 'run', '--no-sync', 'python', '-m', 'daengs_backend.cli.walk_context_backfill', '--limit', $Limit.ToString())
if (-not [string]::IsNullOrWhiteSpace($WalkId)) {
    $arguments += @('--walk-id', ([guid]::Parse($WalkId)).ToString())
} else {
    if ([string]::IsNullOrWhiteSpace($Since) -or [string]::IsNullOrWhiteSpace($Until)) { throw 'An explicit date window is required' }
    $arguments += @('--since', $Since, '--until', $Until)
    if (-not [string]::IsNullOrWhiteSpace($After)) { $arguments += @('--after', ([guid]::Parse($After)).ToString()) }
}
if ($Mode -eq 'Apply') {
    if ($ExpectedPlan -notmatch '^[a-f0-9]{64}$') { throw 'Apply requires a preview digest' }
    $arguments += @('--apply', '--expected-plan', $ExpectedPlan)
}
Invoke-Docker -Arguments $arguments
