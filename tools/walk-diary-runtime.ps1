param(
    [ValidateSet('Configure', 'Prepare', 'Check', 'Start', 'Stop', 'Smoke')]
    [string]$Action = 'Check',
    [double]$Latitude = 37.4878,
    [double]$Longitude = 127.052,
    [ValidateRange(500, 3000)][int]$Radius = 1200,
    [string]$SettingsFile = 'C:/deploy/daengs/walk-public.env'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

if ($Action -eq 'Configure') {
    # One-time, create-only provisioning. Existing server settings are never overwritten.
    if (Test-Path -LiteralPath $SettingsFile) { throw 'Public settings already exist; keep the existing file' }
    $rootEnv = Join-Path $root '.env'
    if (-not (Test-Path -LiteralPath $rootEnv -PathType Leaf)) { throw 'Server root .env is required' }
    $rootText = [IO.File]::ReadAllText($rootEnv)
    if ($rootText -match '(?m)^\s*WALK_PUBLIC_ENV_FILE\s*=') { throw 'Public settings path already configured' }
    $text = [IO.File]::ReadAllText((Join-Path $root 'backend/.env.walk-public.example'))
    foreach ($key in @('SGIS_KEY', 'SGIS_SECRET', 'PUBLIC_DATA_KEY')) {
        $value = [Environment]::GetEnvironmentVariable('WALK_' + $key)
        if ([string]::IsNullOrWhiteSpace($value) -or $value -notmatch '^[A-Za-z0-9+/_%=-]+$') {
            throw ('Missing or invalid credential: WALK_' + $key)
        }
        $text = $text.Replace(('DAENGS_WALK_' + $key + '='), ('DAENGS_WALK_' + $key + '=' + $value))
    }
    $resolved = [IO.Path]::GetFullPath($SettingsFile)
    if ($resolved.Contains("`n") -or $resolved.Contains("`r")) { throw 'Invalid settings path' }
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($resolved)) | Out-Null
    $utf8 = [Text.UTF8Encoding]::new($false)
    [IO.File]::WriteAllText($resolved, $text, $utf8)
    [IO.File]::WriteAllText($rootEnv, $rootText.TrimEnd() + "`nWALK_PUBLIC_ENV_FILE=" + $resolved.Replace('\', '/') + "`n", $utf8)
    Write-Host 'Public settings created with all new flags disabled. Run Prepare next.'
    return
}

# Server only. Do not bootstrap the full stack.
function Invoke-Docker([string[]]$DockerArgs) {
    & docker @DockerArgs
    if ($LASTEXITCODE -ne 0) { throw ('Docker operation failed: ' + $DockerArgs[0]) }
}

# Compose environment overrides env_file even if GEMINI_API_KEY is empty.
if ([string]::IsNullOrWhiteSpace($env:GEMINI_API_KEY)) {
    $appEnv = Join-Path $root 'backend\.env'
    if (Test-Path -LiteralPath $appEnv -PathType Leaf) {
        foreach ($line in [IO.File]::ReadAllLines($appEnv)) {
            if ($line -match '^\s*GEMINI_API_KEY\s*=\s*(.*?)\s*$') {
                $env:GEMINI_API_KEY = $Matches[1].Trim().Trim('"').Trim("'")
            }
        }
    }
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
    Invoke-Docker -DockerArgs @('compose', '--profile', 'walk-diary', 'stop', 'walk-context-beat', 'walk-context-worker')
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
$rootText = [IO.File]::ReadAllText((Join-Path $root '.env'))
if ($rootText -notmatch '(?m)^\s*WALK_PUBLIC_ENV_FILE\s*=\s*(.*?)\s*$') { throw 'Configure the public settings path first' }
$publicPath = $Matches[1].Trim().Trim('"').Trim("'")
if (-not [IO.Path]::IsPathRooted($publicPath)) { $publicPath = Join-Path $root $publicPath }
$publicFile = [IO.Path]::GetFullPath($publicPath)
$env:WALK_PUBLIC_ENV_FILE = $publicFile
$previous = [IO.File]::ReadAllText($publicFile)
$enabled = $previous
foreach ($flag in @('ENTRY_CONTEXT', 'PUBLIC_CONTEXT', 'AREA_CONTEXT', 'DIARY', 'ENTRY_V2', 'ENTRY_V2_WRITE', 'PHOTO_METADATA')) {
    $name = 'DAENGS_WALK_' + $flag + '_ENABLED'
    $enabled = [regex]::Replace($enabled, ('(?m)^\s*' + $name + '\s*=.*\r?\n?'), '')
    $enabled = $enabled.TrimEnd() + "`n" + $name + "=true`n"
}
$utf8 = [Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllText($publicFile, $enabled, $utf8)
try { Invoke-Docker -DockerArgs ($run + $check) }
catch {
    [IO.File]::WriteAllText($publicFile, $previous, $utf8)
    throw
}
Invoke-Docker -DockerArgs @('compose', '--profile', 'walk-diary', 'up', '-d', '--no-deps', 'walk-context-worker')
$ready = $false
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    & docker exec daengs-walk-context-worker sh -c 'uv run --no-sync celery -A daengs_backend.tasks.walk_entry_context:app inspect ping --destination="walk-context@$HOSTNAME" --timeout=2' *> $null
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    Start-Sleep -Seconds 3
}
if (-not $ready) { throw 'Walk worker did not become ready; web was not replaced' }
Invoke-Docker -DockerArgs @('compose', '--profile', 'walk-diary', 'up', '-d', '--no-deps', 'walk-context-beat')
Invoke-Docker -DockerArgs @('compose', 'up', '-d', '--no-deps', 'backend')
Write-Host 'Walk runtime started. Verify a new owned walk through the authenticated API.'
