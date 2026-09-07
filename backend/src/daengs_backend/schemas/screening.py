"""피부 변화 기록 API 의 요청 / 응답 형태 (`/app/screening/*`, D-052).

⚠️ **옛 `/screen/v1/screen` 의 응답과 다른 것입니다.** 그쪽은 판정 결과만 그대로
   돌려주고 아무것도 안 남깁니다. 여기는 **기록**이라 사진과 판정이 한 행으로 묶입니다.

⚠️ `result` 를 열로 펼치지 않습니다. 모델 계약(`contract_version`)이 바뀌어도 옛
   기록은 그대로 남아야 하고, 스키마가 열을 고정하면 그 순간 옛 기록을 못 읽습니다.
   앱은 `contract_version` 을 보고 어떻게 그릴지 정합니다.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

#: 피부 사진으로 받는 형식. 모델·DB CHECK 와 같은 목록입니다.
ScreeningContentType = Literal["image/jpeg", "image/webp"]

ScreeningStatus = Literal["PENDING_UPLOAD", "DONE", "FAILED"]


class ScreeningStartRequest(BaseModel):
    """사진 올릴 자리를 받습니다."""

    #: 어느 아이의 피부인가. **없어도 됩니다** — 아이를 아직 등록 안 했을 수 있습니다.
    #: 남의 아이 id 를 넣으면 404 입니다.
    pet_id: uuid.UUID | None = None

    content_type: ScreeningContentType = "image/jpeg"

    #: 앱의 **가이드 프레임**. 정규화 `[x, y, w, h]` (0~1).
    #:
    #: 주면 학습과 같은 함수로 자릅니다. 안 주면 화면 중앙으로 물러서는데, 1단계는
    #: 큰 차이가 없지만 **2단계 분포가 학습 크롭과 어긋납니다**
    #: (`daengs_screening/service.py` 주석).
    box: list[float] | None = Field(default=None, min_length=4, max_length=4)

    @field_validator("box")
    @classmethod
    def _normalized(cls, v: list[float] | None) -> list[float] | None:
        """0~1 밖의 값은 정규화 좌표가 아닙니다.

        픽셀 좌표를 그대로 보내는 실수를 여기서 잡습니다 — 안 잡으면 크롭이 사진
        바깥을 가리키고, 판정은 그냥 이상한 답을 냅니다(에러가 아닙니다).
        """
        if v is None:
            return v
        if any(not (0.0 <= f <= 1.0) for f in v):
            raise ValueError("box 는 정규화 좌표 [x, y, w, h] (0~1) 여야 합니다.")
        if v[2] <= 0 or v[3] <= 0:
            raise ValueError("box 의 너비·높이는 0보다 커야 합니다.")
        return v


class ScreeningTicketResponse(BaseModel):
    """기록 하나와, 그 사진을 올릴 자리."""

    record_id: uuid.UUID
    storage_key: str
    upload_url: str
    upload_headers: dict[str, str]
    expires_in_seconds: int


class ScreeningRecordResponse(BaseModel):
    """기록 하나."""

    record_id: uuid.UUID
    pet_id: uuid.UUID | None
    status: ScreeningStatus
    created_at: datetime

    #: 판정 결과 원본. `PENDING_UPLOAD` · `FAILED` 면 None 입니다.
    result: dict | None = None

    #: 무엇으로 판정했나. 앱이 옛 기록을 어떻게 그릴지 정하는 열쇠입니다.
    contract_version: str | None = None

    #: 사진을 내려받을 주소. **목록에서는 None 입니다** — N 개마다 저장소를
    #: 두드리게 되므로 단건 조회에서만 만듭니다.
    photo_url: str | None = None


class ScreeningListResponse(BaseModel):
    """**최근 순**입니다. 변화 기록은 늘 최근 것을 위에 놓고 봅니다."""

    records: list[ScreeningRecordResponse]
