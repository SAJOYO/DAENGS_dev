"""도감 카드 API 의 요청 / 응답 형태 (`/app/cards/*`, D-052).

⚠️ **id 를 서버가 만들지 않습니다.** 앱이 만든 UUID 를 경로로 받습니다 — 카드는
   오프라인에서 먼저 만들어지고(로그인 없이 둘러보기로도 뽑습니다) 앱의 Room 에
   이미 그 id 로 들어가 있습니다. 그래서 쓰기는 **PUT upsert** 이고, 같은 카드를
   두 번 올려도 한 장입니다.
"""

import uuid
from datetime import datetime
from typing import Self

from pydantic import BaseModel, Field, model_validator


class DogCardUpsert(BaseModel):
    """카드 한 장을 올립니다. **id 는 경로에 있습니다.**

    부분 수정을 두지 않습니다 — 카드는 뽑힌 뒤로 안 바뀌는 물건이라 고칠 것이
    없습니다. 다시 올리는 것은 "같은 카드를 한 번 더 보내는 것"(멱등)입니다.
    """

    #: 어느 야채인가. **번호가 아니라 문자열**입니다 — 목록 원본이 저쪽 저장소라,
    #: 순서가 바뀌면 번호가 밀려 **어제 뽑은 배추가 오늘 피망이 됩니다.**
    template_id: str = Field(min_length=1, max_length=80)

    #: 어느 아이로 뽑았나. **없어도 됩니다** (아이 없이 뽑을 수 있습니다).
    #: 남의 아이를 지목하면 404 입니다.
    dog_id: uuid.UUID | None = None

    #: 카드에 **인쇄된** 이름. `dog_id` 로 찾는 "지금 이름" 과 다릅니다 —
    #: 개명해도 이미 뽑은 카드의 인쇄는 안 바뀝니다.
    dog_name: str = Field(min_length=1, max_length=40)

    drawn_at: datetime

    #: 번호판 글자. 생일에서 만듭니다.
    code_text: str = Field(default="", max_length=40)

    #: 사용자가 원형 틀에 직접 맞췄나. **false 인 옛 카드는 예전 규칙으로 그립니다.**
    user_framed: bool = False

    #: 또렷한 얼굴만의 자리. **없으면 다음에 열 때 얼굴이 밀립니다.**
    core_left: int
    core_top: int
    core_right: int
    core_bottom: int

    @model_validator(mode="after")
    def _rect(self) -> Self:
        """사각형이 뒤집히면 **그리는 쪽에서 조용히 이상해집니다.**

        DB 에도 같은 CHECK 가 있지만 여기서 막아야 422 로 이유를 말해 줄 수 있습니다 —
        DB 까지 가면 500 입니다 (pet 의 `_birth_date_pair` 와 같은 이유).
        """
        if self.core_right <= self.core_left or self.core_bottom <= self.core_top:
            raise ValueError("core 사각형은 right > left, bottom > top 이어야 합니다.")
        return self


class DogCardResponse(BaseModel):
    """카드 한 장."""

    id: uuid.UUID
    template_id: str
    dog_id: uuid.UUID | None
    dog_name: str
    drawn_at: datetime
    code_text: str
    user_framed: bool
    core_left: int
    core_top: int
    core_right: int
    core_bottom: int

    #: 얼굴 그림이 서버에 있는가. **false 면 앱이 자기 기기의 파일을 씁니다.**
    has_face: bool = False

    #: 얼굴 그림을 내려받을 주소. **목록에서는 None 입니다** — N 장마다 저장소를
    #: 두드리게 되므로 단건 조회에서만 만듭니다.
    face_url: str | None = None


class DogCardUpsertResponse(BaseModel):
    """올린 카드와, 얼굴 그림을 올릴 자리.

    `face_upload` 는 **얼굴이 아직 없을 때만** 옵니다. 이미 올린 카드를 다시 올리면
    None 이고, 앱은 그걸 보고 "이 카드는 다 됐다" 로 판단합니다.
    """

    card: DogCardResponse
    face_upload: "DogCardFaceTicket | None" = None

    #: 이번 요청으로 **새로 생겼는가.** 앱이 동기화 진행률을 세는 데 씁니다.
    created: bool


class DogCardFaceTicket(BaseModel):
    """얼굴 PNG 를 올릴 자리. **PNG 뿐입니다** — 구멍에 끼우려면 알파가 필요합니다."""

    storage_key: str
    upload_url: str
    upload_headers: dict[str, str]
    expires_in_seconds: int


class DogCardListResponse(BaseModel):
    """내 카드 전부. **뽑은 순서의 역순**(최근 것이 위)입니다."""

    cards: list[DogCardResponse]


DogCardUpsertResponse.model_rebuild()
