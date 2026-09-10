"""공동 돌봄 HTTP 경계 — 서비스의 예외를 상태 코드로 바꿉니다 (docs/co-care.md §3).

판단은 여기 없습니다. 라우터가 `CurrentAppUser` 로 잠겨 있습니다.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.schemas.pet_member import (
    InviteAccept,
    InviteCreated,
    InviteListResponse,
    InviteOut,
    MemberListResponse,
    OwnerTransfer,
)
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


@router.get("/pets/{pet_id}/invites", response_model=InviteListResponse)
async def list_invites(pet_id: uuid.UUID, user: CurrentAppUser, session: Session) -> InviteListResponse:
    """살아 있는·이미 쓴 초대 전부. **대표만.** 평문 토큰은 발급 응답에만 있으므로,
    이 목록이 나중에 그 초대를 찾아 취소할 유일한 길입니다.
    """
    try:
        invites = await member_service.list_invites(session, user.app_user_id, pet_id)
    except PetNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _PET_NOT_FOUND) from None
    return InviteListResponse(
        pet_id=pet_id,
        invites=[
            InviteOut(
                id=i.id, expires_at=i.expires_at, created_at=i.created_at,
                accepted_at=i.accepted_at,
            )
            for i in invites
        ],
    )


@router.delete("/pets/{pet_id}/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_invite(
    pet_id: uuid.UUID, invite_id: uuid.UUID, user: CurrentAppUser, session: Session
) -> None:
    """초대 취소. **대표만** — 돌보미·제3자는 강아지가 안 보이므로 404 입니다
    (403 이면 "강아지는 있는데 내 것이 아니다" 가 새 나갑니다).
    """
    try:
        await member_service.cancel_invite(session, user.app_user_id, pet_id, invite_id)
    except PetNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _PET_NOT_FOUND) from None
    except member_service.InviteNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _INVITE_NOT_FOUND) from None


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


@router.get("/pets/{pet_id}/members", response_model=MemberListResponse)
async def list_members(pet_id: uuid.UUID, user: CurrentAppUser, session: Session) -> MemberListResponse:
    """구성원 목록. 대표가 맨 앞입니다. **구성원만 볼 수 있습니다.**"""
    try:
        members = await member_service.list_members(session, user.app_user_id, pet_id)
    except PetNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _PET_NOT_FOUND) from None
    return MemberListResponse(pet_id=pet_id, members=members)


@router.delete("/pets/{pet_id}/members/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    pet_id: uuid.UUID, target_id: uuid.UUID, user: CurrentAppUser, session: Session
) -> None:
    """내보내기(대표) 또는 나가기(본인). **대표는 자기를 못 뺍니다** — 승계로 가야 합니다."""
    try:
        await member_service.remove_member(session, user.app_user_id, pet_id, target_id)
    except PetNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _PET_NOT_FOUND) from None
    except member_service.CannotRemoveOwnerError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "대표는 이 방법으로 나갈 수 없습니다. 다른 보호자에게 대표를 넘기세요.",
        ) from None
    except member_service.NotAllowedError:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "다른 보호자를 내보낼 수 있는 것은 대표뿐입니다."
        ) from None


@router.post("/pets/{pet_id}/owner", status_code=status.HTTP_200_OK)
async def transfer_owner(
    pet_id: uuid.UUID, body: OwnerTransfer, user: CurrentAppUser, session: Session
) -> dict:
    """대표를 넘깁니다. 대상은 **이미 돌보미여야** 합니다."""
    try:
        pet = await member_service.transfer_owner(
            session, user.app_user_id, pet_id, body.app_user_id
        )
    except PetNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _PET_NOT_FOUND) from None
    except member_service.AlreadyOwnerError:
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 이 아이의 대표입니다.") from None
    except member_service.NotAMemberError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "먼저 초대해서 보호자로 참여시키세요."
        ) from None
    except member_service.PetLimitError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "넘겨받는 분이 등록할 수 있는 마릿수를 넘습니다."
        ) from None
    return {"pet_id": str(pet.id), "owner_app_user_id": str(pet.app_user_id)}


__all__ = ["router"]
