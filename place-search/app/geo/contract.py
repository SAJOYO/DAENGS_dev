"""검색 실행 계약. `geo.search` 가 실행하므로 `geo` 가 소유한다 (결정 #67 §3).

**계약 모듈이다** — `core` 와 다른 계약 모듈만 import 하고 로직을 담지 않는다. 지금은
표준 라이브러리 외에 아무것도 import 하지 않는 완전한 잎이다.

`planning/plans.py` 에서 옮겼다. 세 실행자(검색·경로·표시)의 입력이 한 파일에 섞여 있어
`geo.search` 가 자기 입력을 가지러 상위 패키지를 import 해야 했다.
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class SearchMust:
    """만족하지 못하면 **결과에서 빠진다.** 사용자가 명시한 요구만 여기 온다."""

    lat: float
    lng: float
    radius_m: int
    judge_at: datetime                         # 영업 판정 시각 (TimeIntent.service_at 사영)
    kind: str | None = None
    open_now: bool = False
    require_tags: tuple[str, ...] = field(default_factory=tuple)
    exclude_ids: tuple[int, ...] = field(default_factory=tuple)
    limit: int = 20


@dataclass(frozen=True)
class SearchPrefer:
    """**빼지 않는다. 순위만 올린다.**

    야간·응급의 재료는 간판 이름 정규식이다 (`geo/tagging.py`). 실측 2026-08-20 활성 병원
    5,457곳 중 night 1 · emergency 2. 이 신뢰도로는 거를 자격이 없다.

    같은 재료를 쓰던 과목 축은 아예 없앴다 (#64) — 한국 수의 진료에 과목 제도가 없어서
    태그가 자격이 아니라 상호였다. 신뢰도가 낮은 것과 존재하지 않는 것은 다른 처분을 받는다.
    """

    tags: tuple[str, ...] = field(default_factory=tuple)
    confidence: str = "name_regex"       # 어디서 온 신호인지. 표시·감사용


@dataclass(frozen=True)
class SearchPlan:
    must: SearchMust
    prefer: SearchPrefer = field(default_factory=SearchPrefer)
