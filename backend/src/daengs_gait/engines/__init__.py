"""보행 분석 엔진 선택 — backend 와 엔진 사이의 **유일한 이음새** (D-063).

    engine = get_engine(settings.gait_engine)
    record = engine.analyze(local_path)          # AnalysisRecord (contract.py)

의존 방향은 `daengs_backend → daengs_gait` 한쪽뿐입니다. 이 패키지는 `daengs_backend` 를
import 하지 않고, backend 설정값은 **인자로** 받습니다 (D-038 의 규칙 그대로).

**6단계부터 엔진은 `v4` 하나입니다** — legacy 추론 runtime(`pipeline`·`keypoint_infer`·
`crop_assist`·ultralytics)은 제거했습니다. 고르는 자리를 남겨 둔 것은 ① `GAIT_ENGINE` 이
아직 설정에 있고 ② 나중에 다른 추론 조합이 붙을 자리가 여기 하나여야 하기 때문입니다.

⚠️ **옛 legacy 기록은 그대로 읽고 비교합니다.** 없어진 것은 "새 legacy 분석을 실행하는
   능력" 뿐입니다 — 판별은 `daengs_gait.contract`, 비교는 `daengs_gait.compare` 가 계속
   담당합니다 (`services/gait._run_compare` 의 `POSE_MODEL_LEGACY` 분기).

⚠️ 이 모듈은 가벼워야 합니다 — 하위 엔진 모듈은 `get_engine` 안에서만 import 합니다.
   `v4` 는 워커 자신의 인터프리터로 `daengs_gait.inference` 를 **서브프로세스**로 부르므로
   이 프로세스에 torch 가 안 올라옵니다 (D-063 5B).
   backend 웹 프로세스는 이 함수를 부르지 않습니다 — 워커만 부릅니다.
"""

from __future__ import annotations

from daengs_gait.engines.base import Engine

#: `GAIT_ENGINE` 이 받는 값.
ENGINE_NAMES = ("v4",)


def get_engine(name: str, *, v4_timeout_seconds: int | None = None) -> Engine:
    """이름으로 엔진을 고릅니다. 모르는 이름은 **조용히 다른 엔진으로 떨어지지 않고** 예외입니다 —
    잘못 적힌 `GAIT_ENGINE` 이 엉뚱한 엔진으로 돌면서 아무도 모르는 것보다, 분석이 FAILED 사유와
    함께 멈추는 편이 낫습니다. 6단계에서 없어진 `"legacy"` 도 여기서 같은 예외를 받습니다.
    """
    if name == "v4":
        from daengs_gait.engines.subprocess_bridge import SubprocessBridgeEngine

        return SubprocessBridgeEngine(timeout_seconds=v4_timeout_seconds)
    raise ValueError(
        f"알 수 없는 보행 엔진: {name!r} — GAIT_ENGINE 은 {ENGINE_NAMES} 중 하나입니다"
    )


__all__ = ["ENGINE_NAMES", "Engine", "get_engine"]
