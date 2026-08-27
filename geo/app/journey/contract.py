"""이동 실행 계약. `journey.engine` 이 실행하므로 `journey` 가 소유한다 (결정 #67 §3).

**계약 모듈이다** — `core` 와 다른 계약 모듈(`profile.contract`·`providers.base`)만
import 하고 로직을 담지 않는다.

`Companion` 이 여기 있는 이유: `planning/plans.py` 는 순환 import 를 피하려고 이 타입을
`Companion = str` 로 뭉개고 있었다. 계약을 실행자에게 돌려주면 그럴 이유가 없어져
`Literal` 이 복원된다. `profile` 로 보내지 않는 이유는 이게 프로필 종류가 아니라 "이번
이동에 개가 동반하는가"이기 때문이다 — `none` 프로필은 없다.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from app.profile.contract import DogProfile
from app.providers.base import Mode

Companion = Literal["dog", "none"]


@dataclass(frozen=True)
class WalkPlan:
    """도보로 갈 때만 의미 있는 것. 차량 판정에 적용되면 안 된다."""

    max_walk_min: int | None = None      # **개가 걸어도 되는 시간.** 전체 이동시간과 다르다
    # 도보 옵션·피하기는 여기 없다 (#66). 상태에서도 사라졌다.


@dataclass(frozen=True)
class JourneyPlan:
    """어떻게 갈까. 판정에 필요한 **재료와 우선순위**까지만.

    `mode_priority` 가 '선호 수단' 하나가 아닌 이유: 급할 때 차가 없으면 택시, 그것도
    아니면 도보다. 무엇이 실제로 가능한지는 경로를 받아봐야 알기 때문에 순서만 준다.
    """

    origin_lat: float
    origin_lng: float
    resolved_at: datetime
    departure_at: datetime
    companion: Companion = "dog"
    measured: bool = False

    mode_priority: tuple[Mode, ...] = field(default_factory=tuple)
    max_total_min: int | None = None
    hard_limit: bool = False
    walk: WalkPlan = field(default_factory=WalkPlan)

    # --- 상황이 먹인 것
    profile: DogProfile | None = None
    temp_c: float | None = None
