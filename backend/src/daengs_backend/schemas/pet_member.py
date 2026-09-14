"""공동 돌봄 API 의 요청 / 응답 형태 (docs/co-care.md).

앱 짝 PR 과 손으로 맞춥니다. 한쪽만 고치지 마세요.
"""

import uuid
from datetime import datetime
from typing import Self

from pydantic import BaseModel, Field, model_validator


class InviteCreated(BaseModel):
    """**평문 토큰은 여기서 한 번만 나옵니다.** 서버는 해시만 들고 있어 다시 못 보여 줍니다."""

    id: uuid.UUID
    pet_id: uuid.UUID
    token: str
    expires_at: datetime


class InviteBundleCreate(BaseModel):
    """**여러 마리를 토큰 하나에** (`POST /app/pet-invites`, MVP 결정 §2).

    상한은 한 사람이 돌볼 수 있는 마릿수와 같습니다 — 그보다 많이 담아 봐야 받는 쪽이
    수락에서 409 를 받습니다. 여기서 먼저 막아 두면 앱이 이유를 화면에서 압니다.
    """

    pet_ids: list[uuid.UUID] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def _no_duplicates(self) -> Self:
        """같은 아이를 두 번 담을 수 없습니다. DB 의 `pet_invite_pets` PK 도 같은 것을
        막지만, 여기서 걸러야 422 로 이유를 말해 줄 수 있습니다 — DB 까지 가면 500 입니다."""
        if len(set(self.pet_ids)) != len(self.pet_ids):
            raise ValueError("같은 강아지를 두 번 담을 수 없습니다.")
        return self


class InviteBundleCreated(BaseModel):
    """**평문 토큰은 여기서 한 번만 나옵니다.** 서버는 해시만 들고 있어 다시 못 보여 줍니다."""

    id: uuid.UUID
    pet_ids: list[uuid.UUID]
    token: str
    expires_at: datetime


class InviteBundleOut(BaseModel):
    """`GET /app/pet-invites` 의 항목 하나. **토큰도 해시도 담지 않습니다.**"""

    id: uuid.UUID
    #: 담긴 강아지들. 앱이 "맥스·코코를 부른 링크" 로 그립니다.
    pets: list["InvitePetBrief"]
    expires_at: datetime
    created_at: datetime
    #: `None` 이면 아직 아무도 안 눌렀습니다.
    accepted_at: datetime | None


class InvitePetBrief(BaseModel):
    """초대 목록·미리보기에 실리는 강아지 한 마리.

    **건강정보가 없습니다** (MVP 결정 §8) — 수락 전에는 구성원이 아니라, 토큰 하나로 남의
    집 지병·복약을 읽는 자리를 만들면 안 됩니다.
    """

    pet_id: uuid.UUID
    name: str
    breed: str | None = None
    has_photo: bool = False


class InviteBundleListResponse(BaseModel):
    invites: list[InviteBundleOut]


class InvitePreviewRequest(BaseModel):
    """미리보기 요청. **body 로 받습니다** — 토큰을 URL 에 실으면 nginx access log·Referer·
    브라우저 히스토리에 평문이 남습니다 (수락이 body 로 받는 것과 같은 이유)."""

    token: str = Field(min_length=1, max_length=200)


class InvitePreviewPet(InvitePetBrief):
    #: 이미 이 아이의 구성원인가. true 면 수락해도 그 아이는 "이미 충족" 으로 지나갑니다.
    already_member: bool = False


class InvitePreviewResponse(BaseModel):
    """수락 화면이 한 번에 그릴 것 (MVP 결정 §8).

    연결 후보를 따로 부르지 않는 이유는 화면이 "강아지 목록 + 각 줄의 연결 드롭다운" 한
    장이어서입니다 — 나누면 두 응답의 정합성을 앱이 맞춰야 합니다.
    """

    invited_by_nickname: str | None
    expires_at: datetime
    pets: list[InvitePreviewPet]
    #: 내가 고를 수 있는 기존 강아지. 조건은 `pet_repo.list_link_candidates` 에 있습니다.
    link_candidates: list[InvitePetBrief]


class InviteLink(BaseModel):
    """초대에 담긴 아이 하나를 **내 기존 아이와 잇겠다**는 선택 (MVP 결정 §2).

    `link_to_pet_id` 가 `null` 이면 "연결 없이 참여" 입니다 — 항목을 아예 빼는 것과 같습니다.
    """

    pet_id: uuid.UUID
    link_to_pet_id: uuid.UUID | None = None


class InviteAccept(BaseModel):
    #: 초대 링크에 실린 토큰. `pet_id` 를 안 받는 것이 의도입니다 — 수락 전에는 그 강아지에
    #: 아무 권한이 없어서, URL·본문에 실으면 남의 강아지 id 를 넣어 보는 자리가 생깁니다.
    token: str = Field(min_length=1, max_length=200)

    #: 강아지별 연결 선택. **선택 필드입니다** — 없으면 전부 "연결 없이 참여" 이고, 그것이
    #: 구 앱 요청(`{"token": ...}`)과 정확히 같은 동작입니다.
    #:
    #: ⚠️ 여기 실린 id 는 **서버가 다시 검증합니다.** 미리보기가 후보를 내려 줬다는 것만으로
    #: 믿으면, 그 사이 상태가 바뀌었거나 앱이 임의의 id 를 넣은 것을 못 잡습니다.
    links: list[InviteLink] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def _one_choice_per_pet(self) -> Self:
        """같은 초대 강아지에 두 번 선택할 수 없습니다.

        **서비스의 `_validate_link_request` 보다 앞섭니다** — 여기서 걸리면 pydantic 의
        422 가 나가고, 그 뒤의 검증들은 dict `detail` 에 `code` 를 싣습니다. 모양이 다른
        것은 이 하나가 **요청을 dict 로 접기도 전에** 걸리는 자리라서입니다.
        """
        pet_ids = [link.pet_id for link in self.links]
        if len(set(pet_ids)) != len(pet_ids):
            raise ValueError("같은 강아지에 대해 연결 선택이 두 번 왔습니다.")
        return self


class AcceptedPetOut(BaseModel):
    """수락 결과 한 줄 (MVP 결정 §8).

    **`invited_pet_id` 와 `display_pet_id` 를 갈라 둡니다.** 연결했으면 이후 케어·산책
    요청에 쓸 id 는 초대에 담겼던 아이가 아니라 **내 기존 아이**입니다 — 하나로 뭉치면
    받는 사람이 초대한 사람의 행에 기록을 쓰게 됩니다.
    """

    #: 초대 묶음에 담겨 있던 원본 pet 행.
    invited_pet_id: uuid.UUID
    #: 내 화면과 **이후 요청**에 쓸 pet 행. 연결 안 했으면 위와 같습니다.
    display_pet_id: uuid.UUID
    name: str
    #: `linked` · `joined` · `already_member` · `already_owner`
    result: str


class InviteAcceptResponse(BaseModel):
    """묶음 수락 결과.

    최상위 `pet_id`·`name` 은 **구 앱 호환 앵커**입니다 — 옛 계약이 그 두 키를 읽으므로
    지우지 않습니다. 한 마리 초대에서는 `pets[0]` 과 같은 값입니다. **새 앱은 항목별
    `display_pet_id` 를 쓰고 수락 뒤 목록을 새로고침합니다.**
    """

    #: 구 앱 호환 앵커. 앵커 항목의 `display_pet_id` 입니다.
    pet_id: uuid.UUID
    #: 구 앱 호환 앵커. 앵커 항목의 이름입니다.
    name: str
    pets: list[AcceptedPetOut]


class MemberOut(BaseModel):
    app_user_id: uuid.UUID
    #: 지금도 구성원일 때만 이름이 옵니다. 아니면 `None` 이고 앱이 "이전 보호자" 로 그립니다.
    nickname: str | None
    is_owner: bool
    #: `joined_at` 은 일부러 없습니다 — 돌보미는 `list_members` 가 id 만 돌려주고, 대표는 애초에
    #: "가입" 개념이 없어(`pet.created_at` 은 강아지 등록 시각이지 대표의 참여 시각이 아니다) 항상
    #: `None` 일 수밖에 없는 자리였습니다. 값을 못 채우는 필드를 계약에 두면 앱이 그 자리에
    #: null 처리를 둘러 짓게 되므로 아예 뺐습니다 (필드 추가는 하위 호환이지만 삭제는 아니라서,
    #: 값이 생기기 전에 빼는 것이 지금이 제일 쌉니다).


class MemberListResponse(BaseModel):
    pet_id: uuid.UUID
    members: list[MemberOut]


class OwnerTransfer(BaseModel):
    app_user_id: uuid.UUID


class InviteOut(BaseModel):
    """`GET /app/pets/{pet_id}/invites` 의 항목 하나. **토큰도 해시도 담지 않습니다** —
    서버는 해시만 들고 있고, DB 읽기 권한이 있는 사람이라도 이 응답을 그대로 살아 있는
    초대에 쓸 수 있게 하면 안 되기 때문입니다.
    """

    id: uuid.UUID
    expires_at: datetime
    created_at: datetime
    #: `None` 이면 아직 아무도 안 눌렀습니다. 값이 있으면 "이미 쓴 초대" — 대표가 이것으로
    #: 구분해서 그립니다.
    accepted_at: datetime | None


class InviteListResponse(BaseModel):
    pet_id: uuid.UUID
    invites: list[InviteOut]


__all__ = [
    "AcceptedPetOut",
    "InviteAccept",
    "InviteAcceptResponse",
    "InviteBundleCreate",
    "InviteBundleCreated",
    "InviteBundleListResponse",
    "InviteBundleOut",
    "InviteCreated",
    "InviteLink",
    "InviteListResponse",
    "InviteOut",
    "InvitePetBrief",
    "InvitePreviewPet",
    "InvitePreviewRequest",
    "InvitePreviewResponse",
    "MemberListResponse",
    "MemberOut",
    "OwnerTransfer",
]
