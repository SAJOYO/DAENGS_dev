"""Exercise server command ordering with a fake Docker command; never touch a daemon."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.walk.support.paths import REPO


def powershell_command(executable, script, *arguments):
    command = [executable, "-NoProfile"]
    if Path(executable).stem.casefold() == "powershell":
        command.extend(["-ExecutionPolicy", "Bypass"])
    return [*command, "-File", str(script), *arguments]


def run_powershell(command, *, check=False, **kwargs):
    """PowerShell 출력은 **UTF-8 이 아닐 수 있습니다** — 그래서 깨진 바이트를 버립니다.

    `pwsh`(PowerShell 7)는 UTF-8 로 쓰지만 Windows PowerShell 5.1(`powershell.exe`)은
    **콘솔 코드페이지**로 씁니다. 한글 Windows(cp949)에서는 오류 메시지 한 글자가
    `UnicodeDecodeError` 를 일으키는데, 그 예외가 `subprocess` 의 **읽기 스레드** 안에서
    나기 때문에 호출 쪽에는 전파되지 않고 **`stdout`/`stderr` 가 조용히 `None`** 이 됩니다.
    그 뒤 `result.stdout + result.stderr` 가 `TypeError` 로 죽습니다 (#566 ⓐ).

    아래 단언들은 전부 ASCII 조각(`dummysecret` 등)을 찾으므로 `errors="replace"` 로
    충분합니다 — 못 읽은 바이트만 대체 문자가 되고 찾는 문자열은 그대로 남습니다.
    ⚠️ `docker compose config` 는 **진짜 UTF-8** 이라 이 함수를 쓰지 않습니다.
    """
    return subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=check,
        **kwargs,
    )


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
    public = tmp_path / ".env"
    original = "DAENGS_WALK_PUBLIC_CONTEXT_ENABLED=false\nUNCHANGED=value\n"
    public.write_text(original, encoding="utf-8")
    harness = tmp_path / "harness.ps1"
    harness.write_text(
        r"""param([string]$Action, [string]$Reject)
$env:GEMINI_API_KEY = 'local-command-test'
$global:rolloutCalls = [Collections.Generic.List[object]]::new()
$global:rejectCheck = $Reject -eq 'true'
$global:firstPing = $true
function global:Start-Sleep { param([int]$Seconds) }
function global:docker {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$DockerArgs)
    $global:rolloutCalls.Add(@($DockerArgs))
    $global:LASTEXITCODE = 0
    if ($DockerArgs[0] -eq 'ps') { Write-Output 'daengs-backend' }
    if ($DockerArgs[0] -eq 'exec' -and $global:firstPing) {
        $global:firstPing = $false
        Write-Error 'worker is still installing'
        $global:LASTEXITCODE = 1
    }
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
    response = run_powershell(
        powershell_command(pwsh, harness, action, str(reject).lower()),
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
        assert len([call for call in calls if call[0] == "exec"]) == 3
        changed = public.read_text(encoding="utf-8")
        assert changed.count("DAENGS_WALK_PUBLIC_CONTEXT_ENABLED=true") == 1
        assert "DAENGS_WALK_PHOTO_METADATA_ENABLED=true" in changed
        assert "DAENGS_WALK_CATALOG_REFRESH_ENABLED=true" in changed
        assert "DAENGS_WALK_PUBLIC_CATALOG_ROOT=/data/walk-public/regions" in changed
        assert "UNCHANGED=value" in changed
        assert [call[-1] for call in changes] == [
            "walk-context-worker",
            "walk-catalog-worker",
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
                "walk-catalog-worker",
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


@pytest.mark.parametrize("gcp", [False, True])
def test_rendered_compose_isolates_workers_and_shares_public_context(tmp_path, gcp):
    docker = shutil.which("docker")
    if not docker:
        pytest.skip("Docker Compose CLI is not installed; no engine is needed")
    shutil.copyfile(REPO / "docker-compose.yml", tmp_path / "docker-compose.yml")
    (tmp_path / "backend").mkdir()
    # Render a copied project: never read the operator's real .env or echo resolved secrets.
    public = tmp_path / ".env"
    public.write_text("DAENGS_WALK_PUBLIC_CONTEXT_ENABLED=true\nDAENGS_WALK_SGIS_KEY=dummy\nDAENGS_WALK_DIARY_ENABLED=true\nROOT_ONLY_SECRET=never-in-containers\n")
    (tmp_path / "backend/.env").write_text("DAENGS_WALK_DIARY_ENABLED=false\nDAENGS_WALK_SGIS_KEY=old\n")
    (tmp_path / "backend/.env.walk-public.local").write_text("DAENGS_WALK_DIARY_ENABLED=false\n")
    if gcp:
        shutil.copyfile(REPO / "docker-compose.gcp.yml", tmp_path / "docker-compose.gcp.yml")
    env = {
        **os.environ,
        "COMPOSE_PROJECT_NAME": "walk-config-test",
        "COMPOSE_PROFILES": "",
        "COMPOSE_FILE": str(tmp_path / "docker-compose.yml"),
        "REDIS_PASSWORD": "test-only",
        "DAENGS_CORPUS_DIR": str(tmp_path / "corpus"),
        "GEMINI_API_KEY": "test-gemini",
    }

    def render(profiles):
        run = subprocess.run(
            [docker, "compose", "-f", str(tmp_path / "docker-compose.yml"),
             *(["-f", str(tmp_path / "docker-compose.gcp.yml")] if gcp else []),
             *profiles, "config", "--format=json"],
            cwd=tmp_path,
            env=env,
            text=True,
            # compose 는 UTF-8 JSON 을 냅니다. 로케일 기본(한국어 Windows 는 cp949)으로 읽으면
            # crawler-worker command 의 한글에서 UnicodeDecodeError 가 나 stdout 이 None 이 됩니다.
            encoding="utf-8",
            capture_output=True,
            check=True,
            timeout=30,
        )
        return json.loads(run.stdout)["services"]

    assert not {"walk-context-worker", "walk-catalog-worker"} & render([]).keys()
    services = render(["--profile", "*"])
    environments = []
    venvs = []
    for name in (
        "backend",
        "walk-context-worker",
        "walk-context-beat",
        "walk-context-tools",
        "walk-catalog-worker",
    ):
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
        assert volume.get("read_only", False) is (
            name not in {"walk-context-tools", "walk-catalog-worker"}
        )
        venvs.append(next(v["source"] for v in service["volumes"] if v["target"] == "/opt/venv"))
    assert all(value == environments[0] for value in environments)
    assert environments[0]["DAENGS_WALK_SGIS_KEY"] == "dummy"
    for name in ("backend", "walk-context-worker", "walk-context-beat", "walk-context-tools", "walk-catalog-worker"):
        assert services[name]["environment"]["DAENGS_WALK_DIARY_ENABLED"] == "true"
        assert "ROOT_ONLY_SECRET" not in services[name]["environment"]
    # Removing root settings cannot resurrect stale values from backend/.env.
    public.write_text("DAENGS_WALK_DIARY_ENABLED=false\n")
    disabled = render(["--profile", "*"])
    assert disabled["backend"]["environment"]["DAENGS_WALK_DIARY_ENABLED"] == "false"
    assert disabled["backend"]["environment"]["DAENGS_WALK_SGIS_KEY"] == ""
    assert len(set(venvs)) == 5
    # Compose config escapes dollars so the rendered configuration can be reused.
    assert '"$$@"' in services["walk-context-tools"]["entrypoint"][2]
    assert "--queues=walk-entry-context" in " ".join(services["walk-context-worker"]["command"])
    assert "--queues=walk-public-catalog" in " ".join(services["walk-catalog-worker"]["command"])


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
def test_removed_configure_cannot_overwrite_root_env(tmp_path, shell):
    executable = shutil.which(shell)
    if not executable:
        pytest.skip("PowerShell runtime is not installed")
    root_env = tmp_path / ".env"
    root_env.write_text("EXISTING=keep\n")
    result = run_powershell(
        powershell_command(executable, REPO / "tools/walk-diary-runtime.ps1", "-Action", "Configure"),
        cwd=tmp_path, timeout=20,
    )
    assert result.returncode != 0
    assert root_env.read_text() == "EXISTING=keep\n"
