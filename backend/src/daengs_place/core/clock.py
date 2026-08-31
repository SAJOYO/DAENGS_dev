"""요청이 보는 시각의 단일 원천. **어떤 제품 도메인도 모른다.**

`Clock` 이 프로토콜인 이유: `datetime.now()` 를 각자 부르던 게 사달의 뿌리였다. 경로의
야간 판정은 서버 현재 시각을, 병원 영업 판정은 `target.at` 을 봤다 — 같은 요청인데 두
엔진이 다른 시각을 살았다. resolver 를 거치는 요청은 시각을 한 번 정해서 계획에 실어
보낸다 — 다만 `geo.search` 의 직접 호출 경로는 아직 `p.at or SystemClock().now()` 로 자기
시각을 만든다. 그 경로가 resolver 로 합류하기 전까지 이 문장은 절반만 참이다.

`planning/facts.py` 에 있던 것을 옮겼다. 거기 있을 이유가 없었다 — `geo`·`place`·
`journey`·`features` 넷이 이미 planning 에서 꺼내 쓰고 있었고, 그 에지가 `geo ↔ planning`
순환의 한쪽이었다 (결정 #67 PR 2).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True)
class FixedClock:
    """테스트용. '같은 요청은 같은 시각을 본다'를 단언하려면 시간이 고정돼야 한다."""

    at: datetime

    def now(self) -> datetime:
        return self.at
