"""케어 로그 API(`/app/care-events`)의 요청 / 응답 형태 (#332).

앱 짝 PR 은 SAJOYO/DAENGS_APP#201 이고, 응답 모양은 앱 `care/` 와 손으로 맞춥니다.
한쪽만 고치지 마세요.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

#: 모델 쪽 `CARE_EVENT_KINDS` 와 같은 값. **`walk` 가 없습니다** — 산책은 `walks` 가 진실입니다.
CareEventKind = Literal["meal", "medication", "snack"]

#: 챙긴 시각이 지금보다 이만큼 앞서면 오타로 봅니다. 기기 시계가 몇 분 빠른 것은 봐주고,
#: "내일 저녁 약" 을 미리 적는 것은 안 받습니다 — 그것은 기록이 아니라 계획이고, 계획은
#: 로드맵 F5(알림)의 일입니다.
FUTURE_GRACE = timedelta(minutes=10)


def _aware(value: datetime, what: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{what}에는 timezone이 필요합니다.")
    return value


class CareEventCreate(BaseModel):
    """기록 한 건. **멱등키(`client_event_id`)는 앱이 만듭니다** — 탭 두 번·재시도가 두 줄이
    되면 안 됩니다 (`walks.client_session_id` 와 같은 규칙).
    """

    pet_id: uuid.UUID
    kind: CareEventKind

    #: 챙긴 시각. 앱이 보낸 시각이지 서버가 받은 시각이 아닙니다 — "아침에 먹였는데 저녁에
    #: 적는" 경우가 있습니다. timezone 이 없으면 422 입니다: 없는 채로 받으면 어느 하루에
    #: 넣을지 서버가 추측하게 됩니다.
    occurred_at: datetime

    #: 짧은 메모("사료 반만"). 공백뿐이면 None 으로 접습니다 — 빈 문자열을 저장하지 않습니다.
    note: str | None = Field(default=None, max_length=120)

    client_event_id: uuid.UUID

    #: 중복 경고를 이미 보고 "그래도 기록" 을 누른 요청. **쿼리가 아니라 body 인 것이 의도**
    #: 입니다 — 재시도가 같은 body 를 그대로 다시 보내면 됩니다.
    #:
    #: ⚠️ 앱은 이때 **`client_event_id` 를 그대로 둡니다.** 새 키를 만들면 재시도가 두 줄이
    #: 됩니다 (docs/co-care.md §4).
    confirm: bool = False

    @field_validator("occurred_at")
    @classmethod
    def _occurred_at(cls, value: datetime) -> datetime:
        value = _aware(value, "챙긴 시각")
        if value > datetime.now(UTC) + FUTURE_GRACE:
            raise ValueError("챙긴 시각은 지금보다 뒤일 수 없습니다.")
        return value

    @field_validator("note")
    @classmethod
    def _note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class ActorOut(BaseModel):
    """누가 챙겼나. **`nickname` 은 지금도 그 강아지의 구성원일 때만** 옵니다 — 탈퇴자·나간
    돌보미는 `app_user_id` 가 있어도 `nickname` 은 `None` 입니다 (docs/co-care.md §3).
    """

    app_user_id: uuid.UUID | None
    nickname: str | None


class CareEventResponse(BaseModel):
    id: uuid.UUID
    pet_id: uuid.UUID
    kind: CareEventKind
    occurred_at: datetime
    note: str | None
    client_event_id: uuid.UUID
    created_at: datetime
    #: 이 컬럼보다 먼저 쌓인 기록엔 `app_user_id` 자체가 없습니다. 기본값을 두는 것은,
    #: 이 필드가 생기기 전에 이미 있던 호출부·테스트가 `actor` 를 안 채워도 계속 돌게
    #: 하려는 것입니다.
    actor: ActorOut | None = None


class DayWalkOut(BaseModel):
    """그날 그 아이가 나간 산책 한 건 (MVP 결정 §7).

    **`actor` 는 케어 로그와 같은 규칙입니다** — 지금도 그 아이의 구성원일 때만 닉네임이
    옵니다. 산책은 `walks.app_user_id` 가 `NOT NULL` 이라 `app_user_id` 는 언제나 있고,
    비어 있을 수 있는 것은 닉네임뿐입니다 (케어의 `actor` 는 두 칸 다 비어 있을 수 있습니다).
    """

    walk_id: uuid.UUID
    started_at: datetime
    actor: ActorOut


class CareEventQuery(BaseModel):
    """기간 조회의 창. 라우터가 쿼리 문자열에서 만들고 서비스가 상한을 봅니다.

    `from`/`to` 는 파이썬 예약어·이름 충돌 때문에 필드명이 다릅니다 — 쿼리 문자열의 이름은
    라우터가 정합니다.
    """

    start: datetime | None = None
    end: datetime | None = None

    @model_validator(mode="after")
    def _aware_both(self) -> Self:
        if self.start is not None:
            _aware(self.start, "from")
        if self.end is not None:
            _aware(self.end, "to")
        return self


class CareEventListResponse(BaseModel):
    pet_id: uuid.UUID
    #: 실제로 조회한 창. 앱이 안 보내면 서비스 기본값(최근 7일)이 여기 적혀 옵니다.
    start: datetime
    end: datetime
    events: list[CareEventResponse]


class CareDaySummaryResponse(BaseModel):
    """하루 요약 — "밥 2 · 약 1 · 간식 3 · 산책 1" 을 한 번에.

    산책 수는 `walks` 에서 셉니다. 하루의 경계는 `timezone` 기준입니다(기본 Asia/Seoul).
    """

    pet_id: uuid.UUID
    day: date
    timezone: str
    start: datetime
    end: datetime
    meal: int
    medication: int
    snack: int
    walk: int
    #: 그날의 기록 전부(최근 먼저). 화면이 요약 아래에 목록을 그릴 때 두 번 안 부르게.
    events: list[CareEventResponse]

    #: 그날의 산책 한 건씩과 **누가 다녀왔는지** (MVP 결정 §7). `walk` 는 이 목록의 길이라
    #: 둘이 어긋날 수 없습니다.
    #:
    #: 논리 연결된 아이는 두 보호자의 산책이 여기 **섞여** 옵니다 — 그것이 공동 돌봄에서
    #: "퇴근 후 앱을 열면 오늘 일어난 일이 빠짐없이 보인다" 는 뜻입니다. 목록
    #: `GET /app/walks` 는 그대로 **자기 산책만** 보여 줍니다(산책의 소유는 사람 것).
    #:
    #: 기본값이 있는 것은 이 필드가 생기기 전의 호출부·테스트가 계속 돌게 하려는 것입니다
    #: (`CareEventResponse.actor` 와 같은 이유).
    walk_rows: list[DayWalkOut] = Field(default_factory=list)


__all__ = [
    "FUTURE_GRACE",
    "ActorOut",
    "CareDaySummaryResponse",
    "CareEventCreate",
    "CareEventKind",
    "CareEventListResponse",
    "CareEventQuery",
    "CareEventResponse",
    "DayWalkOut",
]
