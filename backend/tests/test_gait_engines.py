"""`daengs_gait.engines` — backend 와 엔진 사이의 이음새 (D-063 2단계).

세 가지를 지킵니다:

1. `get_engine` 이 이름으로 올바른 엔진을 고르고, backend 설정값을 **인자로** 받는다.
   모르는 이름은 조용히 legacy 로 떨어지지 않고 예외다.
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


def test_get_engine_v4_passes_settings_as_arguments(tmp_path: Path) -> None:
    from daengs_gait.engines.v4 import V4Engine

    engine = get_engine(
        "v4", v4_dir=str(tmp_path), v4_python=str(tmp_path / "py"), v4_timeout_seconds=7
    )
    assert isinstance(engine, V4Engine)
    assert engine.name == "v4"
    assert engine.root == tmp_path
    assert engine.configured_python == str(tmp_path / "py")
    assert engine.timeout_seconds == 7


def test_get_engine_v4_default_timeout_is_twenty_minutes() -> None:
    from daengs_gait.engines.v4 import V4_TIMEOUT_SECONDS

    assert get_engine("v4").timeout_seconds == V4_TIMEOUT_SECONDS == 20 * 60


def test_get_engine_legacy() -> None:
    from daengs_gait.engines.legacy import LegacyEngine

    engine = get_engine("legacy")
    assert isinstance(engine, LegacyEngine)
    assert engine.name == "legacy"


def test_get_engine_rejects_unknown_name() -> None:
    """잘못 적힌 GAIT_ENGINE 이 옛 엔진으로 조용히 돌면 아무도 모릅니다 — 멈추는 쪽이 낫습니다."""
    with pytest.raises(ValueError, match="알 수 없는 보행 엔진"):
        get_engine("v5")
    with pytest.raises(ValueError):
        get_engine("")


def test_engine_names_match_get_engine_branches() -> None:
    assert set(engines.ENGINE_NAMES) == {"legacy", "v4"}
    for name in engines.ENGINE_NAMES:
        assert get_engine(name).name == name


def test_legacy_engine_lazily_imports_pipeline_and_disables_persist(monkeypatch, tmp_path):
    """torch 는 `analyze` 를 부를 때만 — 그리고 워커 볼륨에 사본을 남기지 않습니다."""
    import types

    seen: dict = {}

    def process_video(path, *, persist):
        seen["path"], seen["persist"] = path, persist
        return {"quality": {"status": "unavailable"}}

    fake = types.ModuleType("daengs_gait.pipeline")
    fake.process_video = process_video
    monkeypatch.setitem(sys.modules, "daengs_gait.pipeline", fake)

    out = get_engine("legacy").analyze(tmp_path / "input.bin")

    assert out["quality"]["status"] == "unavailable"
    assert seen == {"path": tmp_path / "input.bin", "persist": False}


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
        "leaked = [m for m in ('daengs_gait.engines.legacy', 'daengs_gait.engines.v4', "
        "'daengs_gait.pipeline', 'torch', 'numpy') if m in sys.modules]; "
        "print(','.join(leaked)); sys.exit(1 if leaked else 0)"
    )
    done = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, f"딸려 온 것: {done.stdout.strip() or done.stderr.strip()}"
