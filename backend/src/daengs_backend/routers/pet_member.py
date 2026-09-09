"""공동 돌봄 HTTP 경계 — 서비스의 예외를 상태 코드로 바꿉니다 (docs/co-care.md §3).

판단은 여기 없습니다. 라우터가 `CurrentAppUser` 로 잠겨 있습니다.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.schemas.pet_member import InviteAccept, InviteCreated
from daengs_backend.services import pet_member as member_service
from daengs_backend.services.pet import PetNotFoundError

router = APIRouter(prefix="/app", tags=["pet-members"])

Session = Annotated[AsyncSession, Depends(get_session)]

_PET_NOT_FOUND = "강아지를 찾을 수 없습니다."
_INVITE_NOT_FOUND = "초대를 찾을 수 없습니다."
_INVITE_EXPIRED = "만료된 초대입니다. 새 초대를 요청하세요."


@router.post(
    "/pets/{pet_id}/invites",
    response_model=InviteCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_invite(pet_id: uuid.UUID, user: CurrentAppUser, session: Session) -> InviteCreated:
    """초대 링크 발급. **대표만.** 평문 토큰은 이 응답에만 있습니다.

    내 강아지가 아니면 404 입니다 — 돌보미도 404 라 "대표가 아니다" 가 안 샙니다.
    """
    try:
        invite, token = await member_service.create_invite(session, user.app_user_id, pet_id)
    except PetNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _PET_NOT_FOUND) from None
    except member_service.InviteLimitError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"살아 있는 초대는 {member_service.MAX_ACTIVE_INVITES}개까지입니다.",
        ) from None
    return InviteCreated(
        id=invite.id, pet_id=invite.pet_id, token=token, expires_at=invite.expires_at
    )


@router.post("/pet-invites/accept", status_code=status.HTTP_200_OK)
async def accept_invite(body: InviteAccept, user: CurrentAppUser, session: Session) -> dict:
    """초대 수락. **같은 링크를 두 번 눌러도 200 입니다.**

    경로에 `pet_id` 가 없는 것이 의도입니다 — 수락 전에는 그 강아지에 아무 권한이 없습니다.
    """
    try:
        pet = await member_service.accept_invite(session, user.app_user_id, body.token)
    except member_service.InviteNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _INVITE_NOT_FOUND) from None
    except member_service.InviteExpiredError:
        raise HTTPException(status.HTTP_410_GONE, _INVITE_EXPIRED) from None
    except member_service.AlreadyOwnerError:
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 이 아이의 대표입니다.") from None
    except member_service.MemberLimitError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"한 아이의 보호자는 {member_service.MAX_MEMBERS_PER_PET}명까지입니다.",
        ) from None
    except member_service.PetLimitError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "돌보는 아이가 너무 많습니다."
        ) from None
    return {"pet_id": str(pet.id), "name": pet.name}


__all__ = ["router"]
