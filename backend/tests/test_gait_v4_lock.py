"""v4 엔진의 의존성은 `backend/uv.lock` 의 `gait` 그룹 하나가 정의합니다 (D-063 5A → 5B).

5A 에서 `backend/gait_v4/` 의 자기 lock 을 없애고 `==` 핀을 `gait-v4` 그룹으로 옮겼고, 5B 에서
그 그룹을 legacy 의 `gait` 그룹과 **하나로 합쳤습니다** — 실측으로 병합 전후 lock 의 기존 패키지
버전 변경이 0 이었습니다. 골든(`tests/fixtures/gait/golden_v4_rear.json`, 0.01px)은 **그 버전
조합에서** 나온 것이라 누가 핀을 올리면 여기서 먼저 걸려야 합니다.

지키는 것:
  · `gait` 그룹에 v4 골든 조합의 핀이 **그 버전 그대로** `==` 로 있다 (걸리면 골든을 다시 만들
    결정이 먼저)
  · lock 이 그 핀을 정확히 그 버전으로 풉니다
  · `gait-v4` 그룹·`gait-v4-venv`·`GAIT_V4_PYTHON`·`--only-group gait-v4` 가 되살아나지 않았다
  · GUI 판 opencv 두 개(`opencv-python` · `opencv-contrib-python`)는 override 로 막혀 있다 —
    slim 컨테이너에 libxcb 가 없어 첫 분석에서 죽는다 (2026-08-31 실측). ultralytics·rtmlib
    둘 다 GUI 판을 요구한다
  · compose 의 gait-worker 가 `--group gait` 하나로 venv 하나만 만든다

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


def _gait_group_pins() -> dict[str, str]:
    declared = {}
    for spec in _pyproject()["dependency-groups"]["gait"]:
        name, op, version = spec.partition("==")
        if op:
            declared[name.strip()] = version.strip()
    return declared


def test_gait_group_carries_the_golden_pins_exactly():
    """v4 골든 조합의 8개는 전부 `==` 핀으로 `gait` 그룹 안에 있어야 합니다."""
    assert _gait_group_pins() == V4_PINS


def test_gait_group_no_longer_carries_ultralytics():
    """6단계에서 legacy 추론 runtime 과 함께 빠졌습니다 (D-063). 되살아나면 워커 이미지에
    GUI opencv 를 다시 끌고 오는 자리이므로(위 override 주석) 여기서 잡습니다."""
    names = {spec.split("==")[0].split(">=")[0].strip() for spec in _pyproject()["dependency-groups"]["gait"]}
    assert "ultralytics" not in names, "없앤 ultralytics 가 gait 그룹에 되살아났습니다"


def test_gait_v4_group_is_gone():
    assert "gait-v4" not in _pyproject()["dependency-groups"], "5B 에서 없앤 gait-v4 그룹이 되살아났습니다"


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


def _gait_worker_block() -> str:
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    start = compose.index("\n  gait-worker:\n")
    end = compose.index("\n  territory-vision-worker:\n", start)
    return compose[start:end]


def test_compose_worker_syncs_one_venv_from_the_gait_group():
    block = _gait_worker_block()
    command = block[block.index("command:") :]
    assert "--group gait" in command
    # 5B 에서 없앤 것들 — 되살아나면 두 venv 로 돌아간 것입니다.
    assert "--only-group gait-v4" not in command
    assert "gait-v4-venv" not in block
    assert "/app/gait_v4" not in block
    # ⚠️ `GAIT_V4_*` 는 **하나도** 남지 않아야 합니다. 마지막까지 남아 있던
    # `GAIT_V4_WEIGHTS` 는 서버 `/models/release` 의 두 가중치를 워커 컨테이너 안에서
    # 크기·sha256 으로 대조한 뒤 지웠습니다 — 이제 가중치 경로도 legacy 와 같은
    # `GAIT_RELEASE_DIR` 하나입니다(`daengs_gait.config.RELEASE_DIR`).
    # 주석은 "왜 없앴는가" 를 적어 두는 자리라 세지 않습니다 — **설정 줄**만 봅니다.
    leftovers = [
        line.strip()
        for line in block.splitlines()
        if "GAIT_V4_" in line and not line.strip().startswith("#")
    ]
    assert leftovers == [], f"gait-worker 에 GAIT_V4_* 설정이 남아 있습니다: {leftovers}"
    assert "GAIT_RELEASE_DIR" in block, "가중치 경로 설정이 통째로 사라졌습니다"
    # 옛 해킹 — lock 에 headless 가 있으니 더는 필요 없습니다.
    assert "uv pip install" not in command
    assert "uv pip uninstall" not in command


def test_compose_has_no_gait_v4_volume_or_backend_mount():
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    assert "gait-v4-venv:" not in compose
    assert "backend/gait_v4" not in compose, "backend 서비스의 옛 compare.py 마운트가 남아 있습니다"


def test_dockerfile_uv_supports_group_flags():
    """`--group`/`--frozen` 조합은 uv 0.4.x 부터입니다. 이미지가 그보다 오래되면 워커가 기동에서 죽습니다."""
    dockerfile = (REPO / "docker" / "uv" / "Dockerfile").read_text(encoding="utf-8")
    m = re.search(r"ARG UV_VERSION=(\d+)\.(\d+)\.(\d+)", dockerfile)
    assert m, "docker/uv/Dockerfile 에 UV_VERSION 이 없습니다"
    assert tuple(int(x) for x in m.groups()) >= (0, 5, 0)
