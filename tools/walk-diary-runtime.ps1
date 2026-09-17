param(
    [ValidateSet('Prepare', 'Check', 'Start', 'Stop', 'Smoke')]
    [string]$Action = 'Check',
    [double]$Latitude = 37.4878,
    [double]$Longitude = 127.052,
    [ValidateRange(500, 3000)][int]$Radius = 1200
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

# Server only. Do not bootstrap the full stack.
function Invoke-Docker([string[]]$DockerArgs) {
    & docker @DockerArgs
    if ($LASTEXITCODE -ne 0) { throw ('Docker operation failed: ' + $DockerArgs[0]) }
}

Invoke-Docker -DockerArgs @('compose', 'config', '--quiet')
$containers = @(& docker ps --format '{{.Names}}')
if ($LASTEXITCODE -ne 0) { throw 'Docker is unavailable' }
if ('daengs-backend' -notin $containers) { throw 'Existing backend must be running' }

if ($Action -eq 'Smoke') {
    $target = '/tmp/walk-runtime-smoke-' + [guid]::NewGuid().ToString('N') + '.py'
    try {
        Invoke-Docker -DockerArgs @('cp', (Join-Path $root 'tools/walk_runtime_smoke.py'), ('daengs-backend:' + $target))
        Invoke-Docker -DockerArgs @('exec', '-w', '/app', 'daengs-backend', 'uv', 'run', '--no-sync', 'python', $target, '--execute')
    } finally {
        & docker exec daengs-backend rm -f $target
    }
    return
}

$lat = $Latitude.ToString([Globalization.CultureInfo]::InvariantCulture)
$lng = $Longitude.ToString([Globalization.CultureInfo]::InvariantCulture)
$run = @('compose', 'run', '--rm', '--no-deps', 'walk-context-tools')
$check = @('daengs_backend.cli.walk_runtime_check', '--lat', $lat, '--lng', $lng, '--probe-address')

if ($Action -eq 'Stop') {
    # Queue pause only: pending jobs are retained. See the runbook for flag rollback.
    Invoke-Docker -DockerArgs @('compose', '--profile', 'walk-diary', 'stop', 'walk-context-beat', 'walk-context-worker', 'walk-catalog-worker')
    return
}
if ($Action -eq 'Prepare') {
    Invoke-Docker -DockerArgs ($run + @('daengs_backend.cli.walk_park_catalog'))
    foreach ($kind in @('commerce', 'river')) {
        Invoke-Docker -DockerArgs ($run + @('daengs_backend.cli.walk_area_catalog', $kind, '--lat', $lat, '--lng', $lng, '--radius', $Radius.ToString()))
    }
}
if ($Action -ne 'Start') {
    Invoke-Docker -DockerArgs ($run + $check + @('--allow-disabled'))
    return
}

# Only the explicit Start action enables flags. Restore file on preflight failure.
$publicFile = Join-Path $root '.env'
if (-not (Test-Path -LiteralPath $publicFile -PathType Leaf)) { throw 'Server root .env is required' }
$previous = [IO.File]::ReadAllText($publicFile)
$enabled = $previous
foreach ($flag in @('ENTRY_CONTEXT', 'PUBLIC_CONTEXT', 'AREA_CONTEXT', 'DIARY', 'DIARY_SPACE', 'DIARY_ROUTE_PATTERNS', 'ENTRY_V2', 'ENTRY_V2_WRITE', 'PHOTO_METADATA', 'CATALOG_REFRESH')) {
    $name = 'DAENGS_WALK_' + $flag + '_ENABLED'
    $enabled = [regex]::Replace($enabled, ('(?m)^\s*' + $name + '\s*=.*\r?\n?'), '')
    $enabled = $enabled.TrimEnd() + "`n" + $name + "=true`n"
}
if ($enabled -notmatch '(?m)^\s*DAENGS_WALK_PUBLIC_CATALOG_ROOT\s*=\s*\S+') {
    $enabled = [regex]::Replace($enabled, '(?m)^\s*DAENGS_WALK_PUBLIC_CATALOG_ROOT\s*=.*\r?\n?', '')
    $enabled = $enabled.TrimEnd() + "`nDAENGS_WALK_PUBLIC_CATALOG_ROOT=/data/walk-public/regions`n"
}
$utf8 = [Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllText($publicFile, $enabled, $utf8)
try { Invoke-Docker -DockerArgs ($run + $check) }
catch {
    [IO.File]::WriteAllText($publicFile, $previous, $utf8)
    throw
}
foreach ($worker in @('context', 'catalog')) {
Invoke-Docker -DockerArgs @('compose', '--profile', 'walk-diary', 'up', '-d', '--no-deps', '--force-recreate', ('walk-' + $worker + '-worker'))
$ready = $false
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    # Windows PowerShell turns native stderr into ErrorRecords, even when redirected.
    # A missing Celery binary while uv sync is running is an expected retry here only.
    $previousErrorAction = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $pingCommand = 'uv run --no-sync celery -A daengs_backend.tasks.walk_entry_context:app inspect ping --destination="walk-' + $worker + '@$HOSTNAME" --timeout=2'
        & docker exec ('daengs-walk-' + $worker + '-worker') sh -c $pingCommand *> $null
        $pingExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
    if ($pingExit -eq 0) { $ready = $true; break }
    Start-Sleep -Seconds 3
}
if (-not $ready) { throw ('Walk ' + $worker + ' worker did not become ready; web was not replaced') }
}
Invoke-Docker -DockerArgs @('compose', '--profile', 'walk-diary', 'up', '-d', '--no-deps', '--force-recreate', 'walk-context-beat')
Invoke-Docker -DockerArgs @('compose', 'up', '-d', '--no-deps', '--force-recreate', 'backend')
Write-Host 'Walk runtime started. Verify a new owned walk through the authenticated API.'
