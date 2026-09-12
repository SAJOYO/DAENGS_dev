param(
    [Parameter(Mandatory=$true)][ValidateSet('Inspect','Pause','Resume','Verify')][string]$Action,
    [Parameter(Mandatory=$true)][string]$LiveRoot,
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{40}$')][string]$ExpectedLiveSha
)
$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path -LiteralPath $LiveRoot).Path
$head = git rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $head -ne $ExpectedLiveSha) { throw 'Deployed commit changed; inspect before maintenance' }
git diff --quiet HEAD -- backend/src backend/pyproject.toml backend/uv.lock docker-compose.yml
if ($LASTEXITCODE -ne 0) { throw 'Live application source has local changes' }
Write-Host ('Deployed source: ' + $head)
$worker = 'daengs-territory-vision-worker'
if ($Action -eq 'Pause') {
    docker stop --time 90 $worker
    if ($LASTEXITCODE -ne 0) { throw 'Photo worker stop failed' }
    $running = docker inspect --format '{{.State.Running}}' $worker
    if ($LASTEXITCODE -ne 0 -or $running -ne 'false') { throw 'Photo worker is still running' }
    Write-Host 'Photo worker paused; pending photos remain in the database and broker'
    return
}
if ($Action -eq 'Resume') {
    docker start $worker
    if ($LASTEXITCODE -ne 0) { throw 'Photo worker resume failed' }
    return
}
$commitTime = [DateTimeOffset]::Parse((git show -s --format=%cI HEAD))
if ($LASTEXITCODE -ne 0) { throw 'Cannot read deployed commit timestamp' }
foreach ($name in @('daengs-backend', $worker, 'daengs-crawler-beat')) {
    $raw = docker inspect --format '{{json .State}}' $name
    if ($LASTEXITCODE -ne 0) { throw ('Container inspection failed: ' + $name) }
    $state = ConvertFrom-Json $raw
    Write-Host ($name + ': running=' + $state.Running + ' started=' + $state.StartedAt)
    if ($Action -eq 'Verify') {
        if (-not $state.Running) { throw ('Container is stopped: ' + $name) }
        if ($state.Health -and $state.Health.Status -ne 'healthy') { throw ('Container is not healthy: ' + $name) }
        if ([DateTimeOffset]::Parse($state.StartedAt) -lt $commitTime) { throw ('Container predates deployment: ' + $name) }
    }
}
$probe = Join-Path $PSScriptRoot 'inspect_territory_vision.py'
$target = '/tmp/territory-inspect-' + [Guid]::NewGuid().ToString('N') + '.py'
try {
    docker cp $probe ($worker + ':' + $target)
    if ($LASTEXITCODE -ne 0) { throw 'Probe copy failed' }
    $arguments = @('exec', '-w', '/app', $worker, 'uv', 'run', '--no-sync', 'python', $target)
    if ($Action -eq 'Verify') { $arguments += '--require-ready' }
    & docker @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Territory runtime probe failed' }
} finally {
    docker exec $worker rm -f $target
}
