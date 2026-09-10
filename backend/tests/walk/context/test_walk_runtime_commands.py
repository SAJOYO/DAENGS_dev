"""Exercise server command ordering with a fake Docker command; never touch a daemon."""

import json
import os
import shutil
import subprocess

import pytest

from tests.walk.support.paths import REPO


@pytest.mark.parametrize(
    "action,reject", [("Prepare", False), ("Start", False), ("Start", True), ("Stop", False)]
)
@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
def test_server_commands_and_preflight_gate(tmp_path, action, reject, shell):
    pwsh = shutil.which(shell)
    if not pwsh:
        pytest.skip("PowerShell runtime is not installed")
    (tmp_path / "tools").mkdir()
    shutil.copyfile(
        REPO / "tools/walk-diary-runtime.ps1", tmp_path / "tools/walk-diary-runtime.ps1"
    )
    public = tmp_path / "walk-public.env"
    original = "DAENGS_WALK_PUBLIC_CONTEXT_ENABLED=false\nUNCHANGED=value\n"
    public.write_text(original, encoding="utf-8")
    (tmp_path / ".env").write_text(f"WALK_PUBLIC_ENV_FILE={public.as_posix()}\n", encoding="utf-8")
    harness = tmp_path / "harness.ps1"
    harness.write_text(
        r"""param([string]$Action, [string]$Reject)
$env:GEMINI_API_KEY = 'local-command-test'
$global:rolloutCalls = [Collections.Generic.List[object]]::new()
$global:rejectCheck = $Reject -eq 'true'
function global:docker {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$DockerArgs)
    $global:rolloutCalls.Add(@($DockerArgs))
    $global:LASTEXITCODE = 0
    if ($DockerArgs[0] -eq 'ps') { Write-Output 'daengs-backend' }
    if ($global:rejectCheck -and 'daengs_backend.cli.walk_runtime_check' -in $DockerArgs) {
        $global:LASTEXITCODE = 1
    }
}
$failed = $false
try { & (Join-Path $PSScriptRoot 'tools/walk-diary-runtime.ps1') -Action $Action | Out-Null }
catch { $failed = $true }
@{failed=$failed; calls=@($global:rolloutCalls.ToArray())} | ConvertTo-Json -Depth 6 -Compress
""",
        encoding="utf-8",
    )
    response = subprocess.run(
        [pwsh, "-NoProfile", "-File", str(harness), action, str(reject).lower()],
        text=True,
        capture_output=True,
        check=True,
        timeout=20,
    )
    result = json.loads(response.stdout.splitlines()[-1])
    calls = result["calls"]
    assert result["failed"] is reject
    assert calls[0] == ["compose", "config", "--quiet"]
    changes = [call for call in calls if "up" in call or "stop" in call]
    assert all("--no-deps" in call for call in changes if "up" in call)
    if action == "Start" and not reject:
        changed = public.read_text(encoding="utf-8")
        assert changed.count("DAENGS_WALK_PUBLIC_CONTEXT_ENABLED=true") == 1
        assert "DAENGS_WALK_PHOTO_METADATA_ENABLED=true" in changed
        assert "UNCHANGED=value" in changed
        assert [call[-1] for call in changes] == [
            "walk-context-worker",
            "walk-context-beat",
            "backend",
        ]
        check = next(
            i for i, call in enumerate(calls) if "daengs_backend.cli.walk_runtime_check" in call
        )
        assert all(calls.index(call) > check for call in changes)
    elif action == "Stop":
        assert changes == [
            [
                "compose",
                "--profile",
                "walk-diary",
                "stop",
                "walk-context-beat",
                "walk-context-worker",
            ]
        ]
    else:
        assert not changes
    if reject or action != "Start":
        assert public.read_text(encoding="utf-8") == original
    if action == "Prepare":
        runs = [call for call in calls if "run" in call]
        assert len(runs) == 4
        assert all(
            call[:5] == ["compose", "run", "--rm", "--no-deps", "walk-context-tools"]
            for call in runs
        )
        assert "--allow-disabled" in runs[-1]


def test_rendered_compose_isolates_workers_and_shares_public_context(tmp_path):
    docker = shutil.which("docker")
    if not docker:
        pytest.skip("Docker Compose CLI is not installed; no engine is needed")
    shutil.copyfile(REPO / "docker-compose.yml", tmp_path / "docker-compose.yml")
    (tmp_path / "backend").mkdir()
    # Render a copied project: never read the operator's real .env or echo resolved secrets.
    public = tmp_path / "backend/.env.walk-public.local"
    public.write_text("DAENGS_WALK_PUBLIC_CONTEXT_ENABLED=true\nDAENGS_WALK_SGIS_KEY=dummy\n")
    env = {
        **os.environ,
        "COMPOSE_PROJECT_NAME": "walk-config-test",
        "COMPOSE_PROFILES": "",
        "COMPOSE_FILE": str(tmp_path / "docker-compose.yml"),
        "WALK_PUBLIC_ENV_FILE": str(public),
        "REDIS_PASSWORD": "test-only",
        "DAENGS_CORPUS_DIR": str(tmp_path / "corpus"),
        "GEMINI_API_KEY": "test-gemini",
    }

    def render(profiles):
        run = subprocess.run(
            [docker, "compose", *profiles, "config", "--format=json"],
            cwd=tmp_path,
            env=env,
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
        )
        return json.loads(run.stdout)["services"]

    assert "walk-context-worker" not in render([])
    services = render(["--profile", "*"])
    environments = []
    venvs = []
    for name in ("backend", "walk-context-worker", "walk-context-beat", "walk-context-tools"):
        service = services[name]
        environments.append(
            {
                key: service["environment"][key]
                for key in (
                    "DAENGS_WALK_PUBLIC_CONTEXT_ENABLED",
                    "DAENGS_WALK_SGIS_KEY",
                    "REDIS_URL",
                    "GEMINI_API_KEY",
                    "DAENGS_DB_HOST",
                    "DAENGS_DB_USER",
                    "DAENGS_DB_NAME",
                    "DAENGS_DB_PASSWORD",
                )
            }
        )
        volume = next(v for v in service["volumes"] if v["target"] == "/data/walk-public")
        assert volume["source"] == "walk-public-catalogs"
        assert volume.get("read_only", False) is (name != "walk-context-tools")
        venvs.append(next(v["source"] for v in service["volumes"] if v["target"] == "/opt/venv"))
    assert all(value == environments[0] for value in environments)
    assert len(set(venvs)) == 4
    # Compose config escapes dollars so the rendered configuration can be reused.
    assert '"$$@"' in services["walk-context-tools"]["entrypoint"][2]
    assert "--queues=walk-entry-context" in " ".join(services["walk-context-worker"]["command"])


@pytest.mark.parametrize("valid", [True, False])
@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
def test_configure_is_create_only_and_refuses_incomplete_secrets(tmp_path, valid, shell):
    pwsh = shutil.which(shell)
    if not pwsh:
        pytest.skip("PowerShell runtime is not installed")
    (tmp_path / "tools").mkdir()
    (tmp_path / "backend").mkdir()
    script = tmp_path / "tools/walk-diary-runtime.ps1"
    shutil.copyfile(REPO / "tools/walk-diary-runtime.ps1", script)
    shutil.copyfile(
        REPO / "backend/.env.walk-public.example", tmp_path / "backend/.env.walk-public.example"
    )
    root_env = tmp_path / ".env"
    root_env.write_text("EXISTING=keep\n")
    target = tmp_path / "outside-checkout/public.env"
    env = {
        **os.environ,
        "WALK_SGIS_KEY": "dummykey",
        "WALK_SGIS_SECRET": "dummysecret",
        "WALK_PUBLIC_DATA_KEY": "dummy%3D" if valid else "",
    }
    command = [
        pwsh,
        "-NoProfile",
        "-File",
        str(script),
        "-Action",
        "Configure",
        "-SettingsFile",
        str(target),
    ]
    first = subprocess.run(
        command, env=env, text=True, capture_output=True, timeout=20, check=False
    )
    assert (first.returncode == 0) is valid
    assert "dummysecret" not in first.stdout + first.stderr
    assert target.exists() is valid
    assert "EXISTING=keep" in root_env.read_text()
    if valid:
        value = target.read_bytes()
        second = subprocess.run(
            command, env=env, text=True, capture_output=True, timeout=20, check=False
        )
        assert second.returncode != 0
        assert target.read_bytes() == value
        assert "DAENGS_WALK_PUBLIC_CONTEXT_ENABLED=false" in value.decode()
    else:
        assert root_env.read_text() == "EXISTING=keep\n"
