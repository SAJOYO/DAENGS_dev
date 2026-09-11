"""Execute the actual Actions PowerShell against fake git/docker; no server mutation."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    "mode",
    ["off", "sha", "dirty", "enabled", "db", "live-on", "worker", "schema", "season", "beat", "heartbeat"],
)
def test_prepare_off_gates_and_order(tmp_path, mode):
    workflow = yaml.safe_load((ROOT / ".github/workflows/deploy.yml").read_text("utf-8"))
    job = workflow["jobs"]["prepare_activity_off"]
    assert "PrepareActivityOff" in job["if"]
    assert "inputs.operation == 'Deploy'" in workflow["jobs"]["deploy"]["if"]
    assert not any("uses" in step for step in job["steps"])
    assert workflow["concurrency"] == {"group": "deploy", "cancel-in-progress": False}
    program = job["steps"][0]["run"]
    mock = r"""
$global:calls = [System.Collections.Generic.List[string]]::new()
function git {
    $global:LASTEXITCODE = 0
    if ($args[0] -eq 'rev-parse') {
        if ($env:PREPARE_TEST_MODE -eq 'sha') { 'unexpected' }
        else { $env:EXPECTED_DEPLOYMENT_SHA }
    } elseif ($env:PREPARE_TEST_MODE -eq 'dirty') { $global:LASTEXITCODE = 1 }
}
function docker {
    $global:LASTEXITCODE = 0
    $line = $args -join ' '
    $global:calls.Add($line)
    $mode = $env:PREPARE_TEST_MODE
    if ($line.Contains('config --format json')) {
        $services = @{}
        foreach ($name in @('backend', 'territory-vision-worker', 'activity-worker', 'activity-beat')) {
            $vars = @{ DAENGS_DB_HOST='db'; DAENGS_DB_PORT='5432'; DAENGS_DB_USER='user'; DAENGS_DB_PASSWORD='secret'; DAENGS_DB_NAME='db'; REDIS_URL='redis'; DAENGS_ACTIVITY_GAME_ENABLED='false' }
            if ($name -eq 'activity-beat' -and $mode -eq 'enabled') { $vars.DAENGS_ACTIVITY_GAME_ENABLED = 'true' }
            if ($name -eq 'activity-worker' -and $mode -eq 'db') { $vars.DAENGS_DB_PASSWORD = 'different' }
            $services[$name] = @{environment=$vars}
        }
        @{services=$services} | ConvertTo-Json -Depth 6 -Compress
    } elseif ($line.Contains('python -c')) {
        if ($mode -eq 'live-on') { '1' } else { '0' }
    } elseif ($line.Contains('up -d')) {
        if (($mode -eq 'worker' -and $line.EndsWith('activity-worker')) -or
            ($mode -eq 'beat' -and $line.EndsWith('activity-beat'))) { $global:LASTEXITCODE = 1 }
    } elseif ($line.Contains('cli.activity_runtime')) {
        if ($mode -eq 'schema') { $global:LASTEXITCODE = 1 }
        @{game_enabled=$false; schema_checks=8; active_seasons=[int]($mode -eq 'season')} | ConvertTo-Json -Compress
    } elseif ($line.Contains('cat /tmp/activity-beat.json')) {
        if ($mode -eq 'heartbeat') { '{"state":"leader"}' }
        else { '{"state":"disabled"}' }
    }
}
try {
"""
    script = tmp_path / "prepare.ps1"
    script.write_text(
        mock + program + "\n} catch { $failed=$true }\n@{failed=[bool]$failed; calls=@($global:calls)} | ConvertTo-Json -Compress",
        "utf-8-sig",
    )
    shell = shutil.which("powershell") or shutil.which("pwsh")
    assert shell
    output = subprocess.check_output(
        [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        env={
            **os.environ,
            "PREPARE_TEST_MODE": mode,
            "EXPECTED_DEPLOYMENT_SHA": "a" * 40,
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        },
        text=True,
    )
    result = json.loads(output.splitlines()[-1])
    assert result["failed"] is (mode != "off")
    starts = [line for line in result["calls"] if "up -d" in line]
    expected = 0 if mode in {"sha", "dirty", "enabled", "db", "live-on"} else (
        1 if mode in {"worker", "schema", "season"} else 2
    )
    assert len(starts) == expected
    if starts:
        assert starts[0].endswith("activity-worker")
    if len(starts) == 2:
        assert starts[1].endswith("activity-beat")
    assert "secret" not in output and "different" not in output
