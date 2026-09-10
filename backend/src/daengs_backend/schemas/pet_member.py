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
    "InviteAccept",
    "InviteCreated",
    "InviteListResponse",
    "InviteOut",
    "MemberListResponse",
    "MemberOut",
    "OwnerTransfer",
]
