"""Exercise maintenance guards and allowed mutations in both Windows shells."""

import importlib.util
import json
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from tests.territory.support.paths import REPO


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
@pytest.mark.parametrize("action", ["Inspect", "Pause", "Resume", "Verify"])
@pytest.mark.parametrize("fault", ["none", "head", "dirty", "docker", "stale", "probe"])
def test_guarded_commands(tmp_path, shell, action, fault):
    executable = shutil.which(shell)
    if not executable:
        pytest.skip("PowerShell runtime unavailable")
    harness = tmp_path / "harness.ps1"
    harness.write_text(
        r"""param([string]$Script,[string]$Action,[string]$Fault,[string]$LiveRoot)
$global:runtimeCalls = [Collections.Generic.List[object]]::new()
function global:git {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$CommandArgs)
    $global:LASTEXITCODE = 0
    if ($CommandArgs[0] -eq 'rev-parse') {
        if ($Fault -eq 'head') { return ('b' * 40) }
        return ('a' * 40)
    }
    if ($CommandArgs[0] -eq 'diff' -and $Fault -eq 'dirty') { $global:LASTEXITCODE = 1 }
    if ($CommandArgs[0] -eq 'show') { return '2026-09-12T09:00:00+00:00' }
}
function global:docker {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$CommandArgs)
    $global:runtimeCalls.Add(@($CommandArgs))
    $global:LASTEXITCODE = 0
    if ($Fault -eq 'docker') { $global:LASTEXITCODE = 1; return }
    if ($CommandArgs[0] -eq 'inspect') {
        if ($CommandArgs[2] -eq '{{.State.Running}}') { return 'false' }
        $started = '2026-09-12T09:01:00.123456789Z'
        if ($Fault -eq 'stale') { $started = '2026-09-12T08:59:00.123456789Z' }
        return (@{Running=$true;StartedAt=$started;Health=@{Status='healthy'}} | ConvertTo-Json -Compress)
    }
    if ($CommandArgs[0] -eq 'exec' -and 'python' -in $CommandArgs -and $Fault -eq 'probe') {
        $global:LASTEXITCODE = 1
    }
}
$failed = $false
try { & $Script -Action $Action -LiveRoot $LiveRoot -ExpectedLiveSha ('a' * 40) | Out-Null }
catch { $failed = $true }
@{failed=$failed;calls=@($global:runtimeCalls.ToArray())} | ConvertTo-Json -Depth 6 -Compress
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            executable,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness),
            str(REPO / "tools/territory-vision-runtime.ps1"),
            action,
            fault,
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    report = json.loads(result.stdout.splitlines()[-1])
    expected_failure = (
        fault in {"head", "dirty", "docker"}
        or (fault == "stale" and action == "Verify")
        or (fault == "probe" and action in {"Inspect", "Verify"})
    )
    assert report["failed"] is expected_failure, result.stdout + result.stderr
    calls = report["calls"]
    if fault in {"head", "dirty"}:
        assert calls == []
    mutations = [call for call in calls if call[0] in {"start", "stop"}]
    if mutations:
        assert action in {"Pause", "Resume"}
        assert len(mutations) == 1 and mutations[0][-1] == "daengs-territory-vision-worker"
    if action in {"Inspect", "Verify"} and any(call[0] == "cp" for call in calls):
        assert calls[-1][0:4] == ["exec", "daengs-territory-vision-worker", "rm", "-f"]


@pytest.mark.parametrize(
    "registered",
    [None, ["territory.verify_photo"], ["territory.verify_photo", "territory.recover_photos"]],
)
@pytest.mark.parametrize("columns", [False, True])
@pytest.mark.parametrize("queue", [False, True])
def test_probe_requires_schema_and_actual_worker_registration(
    monkeypatch, registered, columns, queue
):
    spec = importlib.util.spec_from_file_location(
        "territory_probe", REPO / "tools/inspect_territory_vision.py"
    )
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)

    async def inventory():
        return {
            "lease_columns_present": columns,
            "attempt_counts": [],
            "backlog": {"pending_count": 100, "expired_lease_count": 99} if columns else None,
        }

    monkeypatch.setattr(probe, "database_inventory", inventory)
    monkeypatch.setenv("HOSTNAME", "runtime-test")

    def inspector(*, destination, timeout):
        assert destination == ["celery@runtime-test"]
        return SimpleNamespace(
            registered=lambda: {destination[0]: registered} if registered else None,
            active_queues=lambda: (
                {destination[0]: [{"name": "territory-vision"}]} if queue else None
            ),
        )

    monkeypatch.setattr(probe.app.control, "inspect", inspector)
    result = probe.inventory()
    assert result["ready"] is bool(columns and queue and registered and len(registered) == 2)
    assert result["backlog"] is not None if columns else result["backlog"] is None


def test_workflow_uses_isolated_checkout_and_excludes_migration():
    workflow = yaml.safe_load((REPO / ".github/workflows/db-migrate.yml").read_text("utf-8"))
    runtime = workflow["jobs"]["territory_runtime"]
    assert runtime["steps"][0]["with"]["path"] == "_territory-runtime"
    assert runtime["concurrency"]["group"] == "deploy"
    assert "territory_action == 'none'" in workflow["jobs"]["migrate"]["if"]
    assert "territory_action == 'none'" in workflow["jobs"]["inspect_walk"]["if"]
