"""`daengs_gait.engines` — backend 와 엔진 사이의 이음새 (D-063 2단계).

세 가지를 지킵니다:

1. `get_engine` 이 이름으로 올바른 엔진을 고르고, backend 설정값을 **인자로** 받는다.
   모르는 이름은 조용히 다른 엔진으로 떨어지지 않고 예외다 (6단계로 엔진은 v4 하나).
2. 의존 방향 — `daengs_gait` 소스 어디에도 `daengs_backend` import 가 없다.
3. 가벼움 — `daengs_gait.engines` 를 import 해도 하위 엔진 모듈·torch 가 딸려 오지 않는다
   (깨끗한 인터프리터에서 봅니다, `test_gait_app_api.py` 의 프로브와 같은 이유).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from daengs_gait import engines
from daengs_gait.engines import base, get_engine

SRC = Path(__file__).resolve().parents[1] / "src" / "daengs_gait"


def test_get_engine_v4_is_the_subprocess_bridge_on_own_interpreter() -> None:
    """5B: v4 는 별도 venv 가 아니라 워커 자신의 인터프리터로 `daengs_gait.inference` 를
    서브프로세스로 부릅니다 — 설정으로 받는 것은 timeout 뿐입니다."""
    from daengs_gait.engines.subprocess_bridge import SubprocessBridgeEngine

    engine = get_engine("v4", v4_timeout_seconds=7)
    assert isinstance(engine, SubprocessBridgeEngine)
    assert engine.name == "v4"
    assert engine.python == Path(sys.executable)
    assert engine.timeout_seconds == 7


def test_get_engine_v4_default_timeout_is_twenty_minutes() -> None:
    from daengs_gait.engines.subprocess_bridge import V4_TIMEOUT_SECONDS

    assert get_engine("v4").timeout_seconds == V4_TIMEOUT_SECONDS == 20 * 60


def test_get_engine_rejects_the_removed_legacy_name() -> None:
    """6단계에서 legacy **추론 runtime** 을 들어냈습니다. 옛 이름이 남은 설정으로 뜨면
    조용히 도는 대신 멈춰야 합니다 — 옛 legacy **기록**의 조회·비교는 이것과 무관하게
    그대로입니다(`contract.POSE_MODEL_LEGACY` · `compare.compare_loaded_records`)."""
    with pytest.raises(ValueError, match="알 수 없는 보행 엔진"):
        get_engine("legacy")


def test_get_engine_rejects_unknown_name() -> None:
    """잘못 적힌 GAIT_ENGINE 이 옛 엔진으로 조용히 돌면 아무도 모릅니다 — 멈추는 쪽이 낫습니다."""
    with pytest.raises(ValueError, match="알 수 없는 보행 엔진"):
        get_engine("v5")
    with pytest.raises(ValueError):
        get_engine("")


def test_engine_names_match_get_engine_branches() -> None:
    assert set(engines.ENGINE_NAMES) == {"v4"}
    for name in engines.ENGINE_NAMES:
        assert get_engine(name).name == name


def test_engines_satisfy_the_protocol_shape() -> None:
    for name in engines.ENGINE_NAMES:
        engine = get_engine(name)
        assert callable(engine.analyze)
        assert isinstance(engine.name, str)
    assert hasattr(base, "Engine")


def test_daengs_gait_never_imports_daengs_backend() -> None:
    """의존 방향은 backend → daengs_gait 한쪽뿐입니다 (D-038 · D-063). 설정은 인자로 받습니다."""
    offenders = []
    for path in SRC.rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.match(r"\s*(from|import)\s+daengs_backend\b", line):
                offenders.append(f"{path.relative_to(SRC)}:{lineno}")
    assert offenders == []


def test_importing_engines_package_stays_light() -> None:
    """`daengs_gait.engines` 만 import 하면 하위 엔진도 torch 도 안 올라옵니다.

    별도 인터프리터에서 봅니다 — 같은 프로세스의 `sys.modules` 는 앞선 테스트가 무엇을 올렸는지에
    좌우됩니다.
    """
    probe = (
        "import sys; import daengs_gait.engines; "
        "leaked = [m for m in ('daengs_gait.engines.subprocess_bridge', "
        "'daengs_gait.inference.pose', 'torch', 'numpy') if m in sys.modules]; "
        "print(','.join(leaked)); sys.exit(1 if leaked else 0)"
    )
    done = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, f"딸려 온 것: {done.stdout.strip() or done.stderr.strip()}"
