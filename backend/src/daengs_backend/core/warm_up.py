"""임베딩 예열이 어떻게 끝났는지를 프로세스 안에 남깁니다 (#180 · D-021).

**왜 상태 라우터가 아니라 여기냐** — 상태의 원본은 `daengs_life.app.deps` 에 있는데,
`daengs_backend` 가 `daengs_life` 를 import 하는 파일은 `main.py` 와
`orchestration/adapters/life.py` **둘뿐**이고 그것을
`tests/test_main_stays_light.py::test_backend_to_life_imports_stay_at_the_approved_boundaries`
가 AST 로 기계 강제합니다 (D-035 가 어댑터 하나만 새 접점으로 승인하면서
"그 밖의 import 는 여전히 금지" 라고 못박았습니다).

그래서 **이미 예열을 부르고 있는 `main.py` 가** 결과를 여기 담아 `app.state` 에 놓고,
`services/status.py` 는 `daengs_life` 를 모른 채 그것만 읽습니다. 접점은 안 늘어납니다.

D-021 2단계로 `/life/ask` 가 별도 프로세스로 나가면 이 항목은 place·journey 처럼
HTTP 로 물어보는 항목이 됩니다 — 그때 지워질 모듈이라 작게 둡니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

#: `app.state` 에 놓는 이름. 라우터와 `main.py` 가 같은 문자열을 봐야 합니다.
STATE_ATTR = "encoder_warm_up"


class WarmUpPhase(StrEnum):
    """예열이 지금 어디에 있나.

    **`DISABLED` 는 고장이 아닙니다.** `DAENGS_WARM_UP_ENCODER=false` 인 개발 PC 에서는
    아무도 예열하지 않고 **첫 `/life/ask` 요청이 로드를 무는 것이 설계**입니다
    (`daengs_life/app/deps.py` 의 `_WARM_UP_IN_PROGRESS` 주석). 화면에서 이것을 빨갛게
    칠하면 개발 PC 가 늘 고장 나 보입니다.

    **`FAILED` 는 대개 `ml` 그룹이 없는 것입니다.** 그때 죽는 것은 `/life/ask` 하나뿐이고
    로그인도 `/walk` 도 멀쩡합니다 (D-021). 그래서 이 항목만 down 이고 다른 항목은
    영향을 받지 않습니다.
    """

    #: 예열을 끄고 떴다. 첫 `/life/ask` 가 로드를 문다 (설계).
    DISABLED = "disabled"
    #: 올리는 중. 이 동안 들어온 `/life/ask` 는 기다리지 않고 503 + `Retry-After` 다 (#37).
    LOADING = "loading"
    #: 올라왔다.
    READY = "ready"
    #: 못 올렸다. `/life/ask` 만 503 이 된다.
    FAILED = "failed"


@dataclass(frozen=True)
class WarmUp:
    """예열 한 번의 결과. lifespan 이 만들고 갈아 끼웁니다 (frozen 이라 통째로 교체)."""

    phase: WarmUpPhase
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def elapsed_sec(self) -> float | None:
        """올리는 데 걸린 초. 아직 도는 중이면 **지금까지** 걸린 초입니다."""
        if self.started_at is None:
            return None
        end = self.finished_at or datetime.now(UTC)
        return (end - self.started_at).total_seconds()


def now() -> datetime:
    """`datetime.now(UTC)` 한 곳. 테스트가 갈아끼울 수 있게 이름으로 둡니다."""
    return datetime.now(UTC)
