"""반려견 프로필 API 의 요청 / 응답 형태.

이 카드는 등록·조회까지만입니다 — 수정(PATCH)은 다음 profile 카드입니다.
`age` 필드가 없습니다: 나이는 저장하는 순간부터 낡으므로 `birth_date` 를 내보내고
소비자(Place 게이트웨이, 앱 화면)가 계산합니다.
"""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DogSizeClass = Literal["small", "medium", "large"]
DogSex = Literal["M", "F"]


class DogCreateRequest(BaseModel):
    """보호자가 아는 사실만 받습니다. 모르는 것은 안 보내면 됩니다 — 미상은 미상대로.

    `size_class` 만 필수인 이유: 크기 등급은 무게의 함수가 아니라 보호자가 아는
    사실이고(같은 9kg 도 견종에 따라 갈립니다), Place 평가의 최소 재료이기도 합니다.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=50)
    breed: str | None = Field(None, max_length=100)
    birth_date: date | None = None
    sex: DogSex | None = None
    neutered: bool | None = None
    weight_kg: float | None = Field(None, gt=0, le=200)
    size_class: DogSizeClass

    @field_validator("birth_date")
    @classmethod
    def birth_date_is_not_in_the_future(cls, value: date | None) -> date | None:
        if value is not None and value > date.today():
            raise ValueError("birth_date must not be in the future")
        return value

    @field_validator("name", "breed")
    @classmethod
    def strip_and_require_content(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class DogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    breed: str | None
    birth_date: date | None
    sex: DogSex | None
    neutered: bool | None
    weight_kg: float | None
    size_class: DogSizeClass
    created_at: datetime
