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
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class WalkPointUpload(BaseModel):
    """기기가 준 원본 좌표 하나."""

    #: 한 산책 안에서 0부터 매긴 순번. **같은 값을 두 번 보내면 한 줄로 접힙니다.**
    client_seq: int = Field(ge=0)

    #: 일시정지·GPS 점프 뒤 증가합니다. 값이 다른 두 점은 직선으로 이으면 안 됩니다.
    chain_index: int = Field(ge=0)

    at: datetime
    lat: Decimal = Field(ge=-90, le=90)
    lng: Decimal = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    is_mock: bool = False

    @field_validator("at")
    @classmethod
    def _timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("산책 좌표 시각에는 timezone이 필요합니다.")
        return value


class WalkUpload(BaseModel):
    """산책 한 건 통째로. 좌표까지 같이 옵니다."""

    #: 기기의 로컬 DB 세션 id 를 그대로 씁니다. **재시도의 열쇠**입니다 —
    #: 같은 값으로 다시 올리면 서버가 이미 있는 것을 돌려줍니다.
    client_session_id: uuid.UUID

    #: 그 산책에 데리고 나간 아이들. 한 번에 여러 마리를 데리고 나갑니다.
    #:
    #: **빈 목록이어도 됩니다** — 강아지를 등록하기 전에 걸었거나 고르지 않고 나선
    #: 경우입니다. 그래도 산책은 기록입니다. 같은 아이를 두 번 적어도 한 마리이고,
    #: 남의 `pet_id` 는 서버가 조용히 뺍니다.
    #:
    #: 한도는 한 사람이 기를 수 있는 마릿수(`MAX_PETS_PER_USER`)의 두 배입니다 —
    #: 딱 맞춰 두면 상한을 올릴 때 여기를 같이 못 고쳐 요청이 422 로 막힙니다.
    pet_ids: list[uuid.UUID] = Field(default_factory=list, max_length=10)

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


class WalkFinalizeRequest(BaseModel):
    """클라이언트가 끝까지 보냈다고 주장하는 좌표열의 manifest.

    ``client_seq`` 는 산책 안에서 0부터 빠짐없이 증가한다. 서버는 이 선언을 저장된
    chunk의 metadata와 디코딩 결과에 모두 대조한 뒤에만 계산 입력을 봉인한다.

    ``input_fingerprint`` 는 선택 사항이다. 앱이 보내면 서버가 decoded point stream에서
    계산한 같은 v1 지문과 대조한다. 앱이 아직 지문을 만들지 않는 첫 배포에서도 count와
    terminal sequence로 누락·중복·겹침을 막을 수 있다.
    """

    model_config = ConfigDict(extra="forbid")

    expected_point_count: int = Field(ge=0)
    terminal_client_seq: int | None = Field(default=None, ge=0)
    input_fingerprint: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def _terminal_matches_count(self) -> Self:
        if self.expected_point_count == 0:
            if self.terminal_client_seq is not None:
                raise ValueError("빈 좌표열에는 terminal_client_seq가 없어야 합니다.")
            return self
        if self.terminal_client_seq is None:
            raise ValueError("좌표가 있으면 terminal_client_seq가 필요합니다.")
        if self.terminal_client_seq != self.expected_point_count - 1:
            raise ValueError(
                "client_seq는 0부터 연속이어야 하므로 terminal_client_seq는 "
                "expected_point_count - 1이어야 합니다."
            )
        return self


class WalkFinalizeResponse(BaseModel):
    """입력 봉인과 계산 행을 앱이 안정적으로 재시도할 수 있는 요약."""

    walk_id: uuid.UUID
    analysis_id: uuid.UUID
    analysis_state: Literal["derived"] = "derived"
    input_fingerprint: str
    point_count: int
    terminal_client_seq: int | None
    facts_record_version: int
    calculation_version: int
    receipt_version: int
    observation_version: int
    moving_distance_m: int
    moving_s: int
    stop_count: int


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

    #: 그 산책에 나간 아이들. **내 강아지만** 들어 있습니다. 비어 있을 수 있습니다.
    pet_ids: list[uuid.UUID]
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
