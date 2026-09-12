"""Render deployment contracts with dummy configuration; never start the app stack."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


def compose(*extra):
    env = {
        **os.environ,
        "REDIS_PASSWORD": "runtime-test",
        "DAENGS_CORPUS_DIR": str(ROOT),
        "GEMINI_API_KEY": "runtime-test",
        "COMPOSE_PROFILES": "",
    }
    command = ["docker", "compose", "--env-file", os.devnull, "-f", "docker-compose.yml"]
    command.extend(extra)
    return subprocess.check_output(
        command, cwd=ROOT, env=env, text=True, encoding="utf-8", stderr=subprocess.PIPE
    )


def test_default_services_exclude_activity_and_profile_has_isolated_envs():
    assert "activity-worker" not in compose("config", "--services").splitlines()
    value = json.loads(compose("--profile", "activity-game", "config", "--format", "json"))
    services = value["services"]
    names = ("backend", "territory-vision-worker", "activity-worker", "activity-beat")
    for key in (
        "DAENGS_DB_HOST",
        "DAENGS_DB_PORT",
        "DAENGS_DB_USER",
        "DAENGS_DB_NAME",
        "REDIS_URL",
    ):
        assert len({services[n]["environment"][key] for n in names}) == 1
    venvs = []
    for name in ("activity-worker", "activity-beat"):
        svc = services[name]
        assert svc["profiles"] == ["activity-game"]
        assert svc["restart"] == "unless-stopped"
        assert not svc.get("ports")
        assert "--group ml" not in " ".join(svc["command"])
        venvs.append(next(v["source"] for v in svc["volumes"] if v["target"] == "/opt/venv"))
        assert svc["healthcheck"]
    assert len(set(venvs)) == 2
    assert (
        services["activity-beat"]["depends_on"]["activity-worker"]["condition"] == "service_healthy"
    )


@pytest.mark.parametrize("mode", ["off", "active", "partial", "fail-web"])
def test_deploy_preserves_activation_and_stops_on_failure(tmp_path, mode):
    workflow = yaml.safe_load((ROOT / ".github/workflows/deploy.yml").read_text("utf-8"))
    assert workflow["concurrency"]["cancel-in-progress"] is False
    step = next(
        s for s in workflow["jobs"]["deploy"]["steps"] if s["name"].startswith("Compose 서비스")
    )
    program = step["run"].split("# Only maintain the explicitly activated Walk runtime.")[0]
    mock = r"""
$ErrorActionPreference = 'Stop'
$global:calls = [System.Collections.Generic.List[string]]::new()
function docker {
    $global:LASTEXITCODE = 0
    $line = $args -join ' '
    $global:calls.Add($line)
    if ($args[0] -eq 'ps') {
        if ($env:ACTIVITY_DEPLOY_TEST_MODE -eq 'active' -or $env:ACTIVITY_DEPLOY_TEST_MODE -eq 'fail-web') { 'running' }
        elseif ($env:ACTIVITY_DEPLOY_TEST_MODE -eq 'partial' -and $line.Contains('activity-worker')) { 'running' }
    }
    if ($env:ACTIVITY_DEPLOY_TEST_MODE -eq 'fail-web' -and $line.Contains('backend territory-vision-worker')) {
        $global:LASTEXITCODE = 1
    }
}
try {
"""
    script = tmp_path / "deployment.ps1"
    script.write_text(
        mock
        + program
        + "\n} catch { $failed = $true }\n@{failed=[bool]$failed; calls=@($global:calls)} | ConvertTo-Json -Compress",
        "utf-8-sig",
    )
    shell = shutil.which("pwsh") or shutil.which("powershell")
    assert shell, "PowerShell is required for the Windows deployment contract"
    result = json.loads(
        subprocess.check_output(
            [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            env={**os.environ, "ACTIVITY_DEPLOY_TEST_MODE": mode},
            text=True,
        )
    )
    calls = result["calls"]
    changes = [c for c in calls if c.startswith("compose")]
    if mode == "partial":
        assert result["failed"] and changes == []
    elif mode == "off":
        assert not result["failed"]
        assert not any("activity-game" in c for c in changes)
        assert any("--force-recreate" in c and "territory-vision-worker" in c for c in changes)
    else:
        assert changes[0].endswith("stop activity-beat activity-worker")
        if mode == "fail-web":
            assert result["failed"]
            assert not any("activity-game up" in c for c in changes)
        else:
            assert not result["failed"]
            assert changes[-2].endswith("activity-worker")
            assert changes[-1].endswith("activity-beat")
