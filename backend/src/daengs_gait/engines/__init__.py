"""보행 분석 엔진 선택 — backend 와 엔진 사이의 **유일한 이음새** (D-063, 2단계).

    engine = get_engine(settings.gait_engine, v4_dir=…, v4_python=…)
    record = engine.analyze(local_path)          # AnalysisRecord (contract.py)

의존 방향은 `daengs_backend → daengs_gait` 한쪽뿐입니다. 이 패키지는 `daengs_backend` 를
import 하지 않고, backend 설정값은 **인자로** 받습니다 (D-038 의 규칙 그대로).

⚠️ 이 모듈은 가벼워야 합니다 — 하위 엔진 모듈은 `get_engine` 안에서만 import 합니다.
   `legacy` 는 그 안에서 다시 `daengs_gait.pipeline`(torch·ultralytics)을 지연 import 하고,
   `v4` 는 별도 venv 의 서브프로세스라 이 프로세스에 torch 가 안 올라옵니다.
   backend 웹 프로세스는 이 함수를 부르지 않습니다 — 워커만 부릅니다.
"""

from __future__ import annotations

from pathlib import Path

from daengs_gait.engines.base import Engine

#: `GAIT_ENGINE` 이 받는 값. 순서는 의미 없음.
ENGINE_NAMES = ("legacy", "v4")


def get_engine(
    name: str,
    *,
    v4_dir: str | Path | None = None,
    v4_python: str | Path | None = None,
    v4_timeout_seconds: int | None = None,
) -> Engine:
    """이름으로 엔진을 고릅니다. 모르는 이름은 **조용히 legacy 로 떨어지지 않고** 예외입니다 —
    잘못 적힌 `GAIT_ENGINE` 이 옛 엔진으로 돌면서 아무도 모르는 것보다, 분석이 FAILED 사유와
    함께 멈추는 편이 낫습니다.
    """
    if name == "v4":
        from daengs_gait.engines.v4 import V4Engine

        return V4Engine(
            configured_dir=v4_dir,
            configured_python=v4_python,
            timeout_seconds=v4_timeout_seconds,
        )
    if name == "legacy":
        from daengs_gait.engines.legacy import LegacyEngine

        return LegacyEngine()
    raise ValueError(
        f"알 수 없는 보행 엔진: {name!r} — GAIT_ENGINE 은 {ENGINE_NAMES} 중 하나입니다"
    )


__all__ = ["ENGINE_NAMES", "Engine", "get_engine"]
