"""Execute the maintenance PowerShell adapter with fake commands, in both runtimes."""

import json
import os
import shutil
import subprocess

import pytest

from tests.walk.support.paths import REPO


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
@pytest.mark.parametrize(
    "mode,reject",
    [
        ("Preview", None),
        ("Apply", None),
        ("Apply", "digest"),
        ("Preview", "head"),
        ("Migrate", None),
        ("Migrate", "backup"),
        ("Smoke", None),
    ],
)
def test_maintenance_command_boundary(tmp_path, shell, mode, reject):
    executable = shutil.which(shell)
    if not executable:
        pytest.skip("PowerShell runtime unavailable")
    folder = tmp_path / "tools"
    folder.mkdir()
    shutil.copy(REPO / "tools/walk-context-backfill.ps1", folder)
    harness = tmp_path / "harness.ps1"
    harness.write_text(
        r"""
param([string]$Mode, [string]$Reject)
$global:calls = [Collections.Generic.List[object]]::new()
$global:gitCount = 0
$global:reject = $Reject
function global:git {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Values)
    $global:LASTEXITCODE = 0
    if ('rev-parse' -in $Values) {
        $global:gitCount++
        if ($global:reject -eq 'head' -and $global:gitCount -eq 2) { 'different' } else { 'same' }
    }
}
function global:docker {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Values)
    $global:calls.Add(@($Values))
    $global:LASTEXITCODE = 0
    if ($Values[0] -eq 'cp' -and $Values[1] -like 'pgvector:*.dump') {
        if ($global:reject -eq 'backup') { $global:LASTEXITCODE = 1; return }
        [IO.File]::WriteAllBytes($Values[2], [byte[]]::new(2000))
    }
}
$failed = $false
$plan = 'a' * 64
if ($Reject -eq 'digest') { $plan = '' }
try {
    & (Join-Path $PSScriptRoot 'tools/walk-context-backfill.ps1') -Mode $Mode -LiveRoot $PSScriptRoot -Since '2026-09-01T00:00:00+09:00' -Until '2026-09-10T00:00:00+09:00' -Limit 1 -ExpectedPlan $plan -BackupDirectory (Join-Path $PSScriptRoot 'backup') | Out-Null
} catch { $failed = $true }
@{failed=$failed; calls=@($global:calls.ToArray())} | ConvertTo-Json -Depth 6 -Compress
""",
        encoding="utf-8",
    )
    env = dict(os.environ, PSExecutionPolicyPreference="Bypass")
    result = subprocess.run(
        [executable, "-NoProfile", "-File", str(harness), mode, reject or ""],
        capture_output=True,
        text=True,
        check=True,
        timeout=25,
        env=env,
    )
    report = json.loads(result.stdout.splitlines()[-1])
    assert report["failed"] is (reject is not None)
    calls = report["calls"]
    if mode == "Migrate":
        sql = [c for c in calls if any("exec psql" in s for s in c)]
        assert bool(sql) is (reject is None)
        if sql:
            assert "--single-transaction" in sql[0][-1]
            backup = next(i for i, c in enumerate(calls) if c[0] == "cp" and c[1].endswith(".dump"))
            assert backup < calls.index(sql[0])
        assert not any(c[:2] == ["compose", "up"] for c in calls)
    elif reject:
        assert not calls
    else:
        run = next(c for c in calls if c[:2] == ["exec", "-w"])
        assert run[2:8] == ["/app", "daengs-backend", "uv", "run", "--no-sync", "python"]
        if mode == "Apply":
            assert run[-3:] == ["--apply", "--expected-plan", "a" * 64]
        elif mode == "Preview":
            assert "--apply" not in run
        else:
            assert run[-2:] == ["--execute", "--backfill"]
