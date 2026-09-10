"""v4 엔진의 의존성은 `backend/uv.lock` 하나가 정의합니다 (D-063 5A).

`backend/gait_v4/` 의 자기 `pyproject.toml`·`uv.lock` 은 5A 에서 없앴고, 그 `==` 핀은 backend
`pyproject.toml` 의 `gait-v4` 그룹으로 옮겼습니다. 골든(`gait_v4/tests/golden_v4_rear.json`, 0.01px)은
**그 버전 조합에서** 나온 것이라 누가 핀을 올리면 여기서 먼저 걸려야 합니다.

지키는 것:
  · `gait-v4` 그룹의 핀이 lock 에 **그 버전 그대로** 있다 (걸리면 골든을 다시 만들 결정이 먼저)
  · GUI 판 opencv 두 개(`opencv-python` · `opencv-contrib-python`)는 override 로 막혀 있다 — slim 컨테이너에
    libxcb 가 없어 첫 분석에서 죽는다 (2026-08-31 실측). ultralytics·rtmlib 둘 다 GUI 판을 요구한다
  · compose 의 gait-worker 가 그 그룹으로 v4 venv 를 만들고, 옛 "uninstall → install --no-deps" 해킹이 없다
  · `gait_v4/` 에 자기 프로젝트 파일이 되살아나지 않았다

lock 파일을 파싱만 합니다 — 설치·네트워크 없음.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent

# 골든이 나온 조합 (walk_demo `port/gait_v4` 의 pyproject 그대로 — GUI opencv 만 headless 로).
V4_PINS = {
    "torch": "2.13.0",
    "torchvision": "0.28.0",
    "onnxruntime": "1.29.0",
    "rtmlib": "0.0.16",
    "opencv-python-headless": "5.0.0.93",
    "opencv-contrib-python-headless": "5.0.0.93",
    "numpy": "2.5.2",
    "imageio-ffmpeg": "0.6.0",
}


def _pyproject() -> dict:
    return tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))


def _lock_versions() -> dict[str, set[str]]:
    lock = tomllib.loads((BACKEND / "uv.lock").read_text(encoding="utf-8"))
    out: dict[str, set[str]] = {}
    for pkg in lock["package"]:
        out.setdefault(pkg["name"], set()).add(pkg.get("version", ""))
    return out


def test_gait_v4_group_pins_match_the_golden_combination():
    group = _pyproject()["dependency-groups"]["gait-v4"]
    declared = {}
    for spec in group:
        name, _, version = spec.partition("==")
        declared[name.strip()] = version.strip()
    assert declared == V4_PINS


def test_lock_resolves_the_pins_to_exactly_those_versions():
    lock = _lock_versions()
    for name, version in V4_PINS.items():
        assert name in lock, f"{name} 이 uv.lock 에 없습니다 — `uv lock` 을 다시 돌리세요"
        # torch 같은 것은 `+cpu` / `+cu126` 로컬 버전이 같이 있습니다. 기본 버전이 핀과 같아야 합니다.
        bases = {v.split("+")[0] for v in lock[name]}
        assert bases == {version}, f"{name}: lock {sorted(lock[name])} vs 핀 {version}"


def test_gui_opencv_is_blocked_by_override():
    overrides = _pyproject()["tool"]["uv"]["override-dependencies"]
    blocked = {o.split(";")[0].strip() for o in overrides if "sys_platform == 'never'" in o}
    assert {"opencv-python", "opencv-contrib-python"} <= blocked


def test_compose_worker_syncs_v4_from_the_backend_lock_group():
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    start = compose.index("\n  gait-worker:\n")
    end = compose.index("\n  territory-vision-worker:\n", start)
    block = compose[start:end]
    command = block[block.index("command:") :]
    assert "--only-group gait-v4" in command
    assert "--no-install-project" in command
    # 옛 해킹 — lock 에 headless 가 있으니 더는 필요 없습니다.
    assert "uv pip install" not in command
    assert "uv pip uninstall" not in command
    assert "cd /app/gait_v4" not in command


def test_gait_v4_has_no_project_files_of_its_own():
    v4 = BACKEND / "gait_v4"
    for name in ("pyproject.toml", "uv.lock", "requirements.txt"):
        assert not (v4 / name).exists(), (
            f"gait_v4/{name} 이 되살아났습니다 — 의존성 정본은 backend/uv.lock 입니다"
        )


def test_dockerfile_uv_supports_only_group():
    """`--only-group` 은 uv 0.4.x 에서 생겼습니다. 이미지가 그보다 오래되면 워커가 기동에서 죽습니다."""
    dockerfile = (REPO / "docker" / "uv" / "Dockerfile").read_text(encoding="utf-8")
    m = re.search(r"ARG UV_VERSION=(\d+)\.(\d+)\.(\d+)", dockerfile)
    assert m, "docker/uv/Dockerfile 에 UV_VERSION 이 없습니다"
    assert tuple(int(x) for x in m.groups()) >= (0, 5, 0)
