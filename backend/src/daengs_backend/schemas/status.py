"""상태 페이지 API 의 응답 형태 (#180 · 로드맵 B1).

**항목 목록 하나**입니다. 항목마다 모양을 다르게 두지 않습니다 — 화면이 그것을 표로
그리는데, DB 는 이 필드, Redis 는 저 필드 식이면 항목이 하나 늘 때마다 프론트를 고쳐야
합니다. 숫자(예산 잔량 · 마지막 크롤 시각)는 `detail` 문장 안에 넣습니다.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class StatusState(StrEnum):
    """네 값입니다. **`absent` 와 `down` 을 가르는 것이 이 화면의 핵심**입니다.

    로컬 서버와 GCP 는 같은 코드가 뜨는데 있는 것이 다릅니다 — GCP 에는 크롤러가 없고
    (`docs/deploy/roadmap.md` §2-4), gait 는 어디서도 profile 로 꺼져 있습니다 (D-038).
    그것을 `down` 으로 칠하면 화면이 늘 빨갛고, 빨간 게 늘 있으면 아무도 안 봅니다
    (로드맵 §6 "환경 차이는 API 가 알려 주는 것으로 화면이 안내한다").
    """

    #: 정상.
    OK = "ok"
    #: 동작은 하는데 덜 준비됐다. 사람이 당장 할 일은 없다.
    DEGRADED = "degraded"
    #: **있어야 하는데 죽었다.** 사람이 볼 자리.
    DOWN = "down"
    #: **이 환경엔 없다.** 고장이 아니다.
    ABSENT = "absent"


class StatusItemOut(BaseModel):
    """항목 하나."""

    #: 기계 이름. 프론트가 key 로 씁니다. 바뀌면 화면이 항목을 잃습니다.
    name: str
    #: 화면에 쓰는 이름. **백엔드가 줍니다** — 프론트에 이름표를 두면 항목이 늘 때
    #: 프론트를 같이 고쳐야 하고, 안 고치면 낯선 id 가 그대로 화면에 뜹니다.
    label: str
    state: StatusState
    #: 사람이 읽는 한 문장. 왜 그 상태인지와 숫자가 여기 들어갑니다.
    detail: str


class StatusOut(BaseModel):
    """화면의 기본 응답. 30초마다 폴링합니다."""

    items: list[StatusItemOut]
    #: 언제 본 값인가. 폴링이 멈춰도 화면이 그것을 알 수 있게 합니다.
    checked_at: datetime
