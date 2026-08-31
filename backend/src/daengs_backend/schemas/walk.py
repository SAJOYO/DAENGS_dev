"""산책 기록 API 의 요청 / 응답 형태.

**끝난 산책만 받습니다.** 진행 중인 것을 올릴 길을 두지 않는 이유는, 그러면 서버가
"아직 안 끝난 산책"을 들고 있다가 기기가 죽으면 영영 안 끝나는 행이 남기 때문입니다.
기기가 끝을 보고 나서 한 번에 올립니다.

**모르는 것은 `None` 입니다.** 날씨를 못 받은 산책이 그렇고, 그걸 "맑음"으로 채우지
않습니다 (`schemas/pet.py` 와 같은 원칙).
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Self

from pydantic import BaseModel, Field, model_validator


class WalkPointUpload(BaseModel):
    """기기가 준 원본 좌표 하나."""

    #: 한 산책 안에서 0부터 매긴 순번. **같은 값을 두 번 보내면 한 줄로 접힙니다.**
    client_seq: int = Field(ge=0)

    #: 일시정지·GPS 점프 뒤 증가합니다. 값이 다른 두 점은 직선으로 이으면 안 됩니다.
    chain_index: int = Field(ge=0)

    at: datetime
    lat: Decimal = Field(ge=-90, le=90)
    lng: Decimal = Field(ge=-180, le=180)
    accuracy_m: float | None = None
    is_mock: bool = False


class WalkUpload(BaseModel):
    """산책 한 건 통째로. 좌표까지 같이 옵니다."""

    #: 기기의 로컬 DB 세션 id 를 그대로 씁니다. **재시도의 열쇠**입니다 —
    #: 같은 값으로 다시 올리면 서버가 이미 있는 것을 돌려줍니다.
    client_session_id: uuid.UUID

    #: 누구와 걸었나. 등록한 강아지가 없으면 `None` 입니다 —
    #: **아무 강아지나 갖다 붙이지 않습니다.**
    pet_id: uuid.UUID | None = None

    started_at: datetime
    ended_at: datetime

    weather_code: int | None = None
    is_day: bool | None = None
    temperature_c: Decimal | None = Field(default=None, decimal_places=1)

    points: list[WalkPointUpload] = Field(default_factory=list)

    @model_validator(mode="after")
    def _time_order(self) -> Self:
        """끝이 시작보다 앞설 수 없습니다.

        DB 에도 같은 CHECK 가 있지만 여기서 막아야 500 이 아니라 422 로 이유를
        말해 줄 수 있습니다 (`PetUpsert` 와 같은 이유).
        """
        if self.ended_at < self.started_at:
            raise ValueError("ended_at 은 started_at 보다 앞설 수 없습니다.")
        return self

    @model_validator(mode="after")
    def _unique_seq(self) -> Self:
        """한 요청 안에서 `client_seq` 가 겹치면 안 됩니다.

        겹친 채로 넣으면 DB 가 PK 위반으로 통째로 실패합니다. 어느 점이 문제인지
        여기서 말해 주는 편이 낫습니다.
        """
        seqs = [point.client_seq for point in self.points]
        if len(seqs) != len(set(seqs)):
            raise ValueError("client_seq 가 겹칩니다.")
        return self


class WalkPointsAppend(BaseModel):
    """좌표만 이어 붙입니다. **긴 산책을 나눠 올릴 때** 씁니다.

    두 시간 산책이면 좌표가 5천 점 가까이 되고, 좌표가 촘촘히 잡히면 만 점도 넘습니다
    (JSON 1MB 초과). 그러면 nginx 기본 바디 한도에 걸려 **그 산책이 영영 안 올라갑니다.**

    나눠 보내도 안전한 이유는 `client_seq` 가 PK 의 일부라서입니다 — 같은 점을 두 번
    보내도 한 줄이고, 순서가 뒤바뀌어 도착해도 결과가 같습니다.
    """

    points: list[WalkPointUpload] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_seq(self) -> Self:
        seqs = [point.client_seq for point in self.points]
        if len(seqs) != len(set(seqs)):
            raise ValueError("client_seq 가 겹칩니다.")
        return self


class WalkPointResponse(BaseModel):
    client_seq: int
    chain_index: int
    at: datetime
    lat: Decimal
    lng: Decimal
    accuracy_m: float | None
    is_mock: bool


class WalkResponse(BaseModel):
    """목록에 쓰는 모양. **좌표가 없습니다.**

    목록에 좌표까지 실으면 산책 스무 건에 좌표 수만 개가 딸려 옵니다. 경로는 한 건을
    열 때만 필요합니다.
    """

    id: uuid.UUID
    client_session_id: uuid.UUID
    pet_id: uuid.UUID | None
    started_at: datetime
    ended_at: datetime
    weather_code: int | None
    is_day: bool | None
    temperature_c: Decimal | None


class WalkDetailResponse(WalkResponse):
    """한 건 + 좌표. `client_seq` 순서로 옵니다."""

    points: list[WalkPointResponse]


class WalkListResponse(BaseModel):
    walks: list[WalkResponse]
