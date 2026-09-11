"""import 경계 — backend 웹이 v4 비교를 쓰려고 `daengs_gait` 를 import 해도 무거운 추론 런타임이
따라오지 않는다 (D-063 5B 재점검 #9).

5B 전에는 `gait_v4/compare.py` 를 **파일로** 불러 이 문제를 피했습니다(패키지 `__init__` 이
torch 를 끌고 왔으므로). 이제 `daengs_gait.compare_v4` 를 평범하게 import 하는데, 그게
가능한 이유는 ① `daengs_gait/__init__.py` · `inference/__init__.py` 에 eager import 가 없고
② `compare_v4` 가 numpy 와 `daengs_gait.compare` 만 쓰며 ③ `inference/model.py` 가 `os`·
`pathlib`·`daengs_gait.config` 뿐이기 때문입니다. 셋 중 하나가 무너지면 backend 웹 컨테이너
(기본 설치 — torch·cv2 없음)가 비교 요청에서 ImportError 500 을 냅니다.

**깨끗한 인터프리터에서 봅니다** — 같은 프로세스의 `sys.modules` 는 앞선 테스트가 무엇을
올렸는지에 좌우됩니다(`test_gait_app_api.py` 의 프로브와 같은 이유).
"""

from __future__ import annotations

import subprocess
import sys

import pytest

HEAVY = ("torch", "torchvision", "rtmlib", "onnxruntime", "cv2", "ultralytics", "imageio_ffmpeg")


def _probe(imports: str) -> list[str]:
    code = (
        "import sys; " + imports + "; "
        f"leaked = [m for m in {HEAVY!r} if m in sys.modules]; "
        "print(','.join(leaked)); sys.exit(1 if leaked else 0)"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    if done.returncode not in (0, 1):
        pytest.fail(f"프로브 자체가 죽음: {done.stderr.strip()[-800:]}")
    return [m for m in done.stdout.strip().split(",") if m]


def test_web_compare_path_does_not_pull_inference_runtime() -> None:
    """`services/gait._load_v4_compare` 가 도는 길 — backend 웹 프로세스의 실제 import."""
    leaked = _probe(
        "import daengs_backend.services.gait as s; fn = s._load_v4_compare(); "
        "assert fn.__module__ == 'daengs_gait.compare_v4', fn.__module__"
    )
    assert leaked == [], f"v4 비교 import 에 딸려 온 것: {leaked}"


def test_daengs_gait_package_and_inference_init_stay_light() -> None:
    """`daengs_gait` · `daengs_gait.inference` · `inference.model` 만 import — heavy 0."""
    leaked = _probe(
        "import daengs_gait, daengs_gait.inference, daengs_gait.inference.model, "
        "daengs_gait.compare_v4, daengs_gait.contract, daengs_gait.engines"
    )
    assert leaked == [], f"가벼워야 할 모듈이 끌고 온 것: {leaked}"


def test_inference_pose_is_the_only_place_heavy_runtime_enters() -> None:
    """반대 방향의 확인 — 자식 프로세스가 도는 `inference.pose` 를 import 하면 heavy 가 실제로
    올라옵니다. 이 테스트가 skip 되면(런타임 미설치) 위 두 테스트는 아무것도 증명하지 않으므로
    같이 봅니다."""
    pytest.importorskip("cv2")
    pytest.importorskip("numpy")
    leaked = _probe("import daengs_gait.inference.pose")
    assert "cv2" in leaked, "inference.pose 가 cv2 를 안 쓰는 것으로 보임 — 프로브가 낡았을 수 있음"
