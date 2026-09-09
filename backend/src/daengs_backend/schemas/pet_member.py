"""공동 돌봄 API 의 요청 / 응답 형태 (docs/co-care.md).

앱 짝 PR 과 손으로 맞춥니다. 한쪽만 고치지 마세요.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class InviteCreated(BaseModel):
    """**평문 토큰은 여기서 한 번만 나옵니다.** 서버는 해시만 들고 있어 다시 못 보여 줍니다."""

    id: uuid.UUID
    pet_id: uuid.UUID
    token: str
    expires_at: datetime


class InviteAccept(BaseModel):
    #: 초대 링크에 실린 토큰. `pet_id` 를 안 받는 것이 의도입니다 — 수락 전에는 그 강아지에
    #: 아무 권한이 없어서, URL·본문에 실으면 남의 강아지 id 를 넣어 보는 자리가 생깁니다.
    token: str = Field(min_length=1, max_length=200)


class MemberOut(BaseModel):
    app_user_id: uuid.UUID
    #: 지금도 구성원일 때만 이름이 옵니다. 아니면 `None` 이고 앱이 "이전 보호자" 로 그립니다.
    nickname: str | None
    is_owner: bool
    joined_at: datetime | None


class MemberListResponse(BaseModel):
    pet_id: uuid.UUID
    members: list[MemberOut]


class OwnerTransfer(BaseModel):
    app_user_id: uuid.UUID


__all__ = [
    "InviteAccept",
    "InviteCreated",
    "MemberListResponse",
    "MemberOut",
    "OwnerTransfer",
]
