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
    InviteBundleCreate,
    InviteBundleCreated,
    InviteBundleListResponse,
    InviteBundleOut,
    InviteCreated,
    InviteListResponse,
    InviteOut,
    InvitePetBrief,
    InvitePreviewPet,
    InvitePreviewRequest,
    InvitePreviewResponse,
    MemberListResponse,
    OwnerTransfer,
)
from daengs_backend.services import pet_identity as identity_service
from daengs_backend.services import pet_member as member_service
from daengs_backend.services.pet import PetNotFoundError

router = APIRouter(prefix="/app", tags=["pet-members"])

Session = Annotated[AsyncSession, Depends(get_session)]

_PET_NOT_FOUND = "강아지를 찾을 수 없습니다."
_INVITE_NOT_FOUND = "초대를 찾을 수 없습니다."
_INVITE_EXPIRED = "만료된 초대입니다. 새 초대를 요청하세요."
#: 묶음 구성이 바뀌었습니다. **만료와 문구를 가르는 이유**는 사용자가 할 일이 달라서입니다 —
#: 만료는 "시간이 지났다" 이고 이쪽은 "담긴 아이가 바뀌었다" 입니다. 둘 다 410 입니다.
_INVITE_BUNDLE_CHANGED = "초대에 담긴 아이가 바뀌었어요. 새 초대를 요청하세요."


def _not_group_owner(exc: identity_service.NotGroupOwnerError) -> HTTPException:
    """그룹 관리 동작을 그룹 주보호자가 아닌 사람이 불렀습니다 → **409**
    (`routers/pet.py` 의 같은 헬퍼와 같은 모양·같은 이유)."""
    return HTTPException(
        status.HTTP_409_CONFLICT,
        {
            "code": "not_group_owner",
            "message": f"이 아이의 주 보호자만 할 수 있어요 ({exc.pet_name}).",
            "pet_name": exc.pet_name,
        },
    )


def _brief(pet, **extra) -> dict:
    """초대 목록·미리보기에 실을 최소 정보. **건강정보는 안 넣습니다** (MVP 결정 §8)."""
    return {
        "pet_id": pet.id,
        "name": pet.name,
        "breed": pet.breed,
        "has_photo": pet.photo_storage_key is not None,
        **extra,
    }


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
    except identity_service.NotGroupOwnerError as exc:
        raise _not_group_owner(exc) from None
    except member_service.PetFarewelledError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"배웅한 아이는 초대할 수 없어요 ({exc.pet_name}).",
        ) from None
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
    except identity_service.NotGroupOwnerError as exc:
        raise _not_group_owner(exc) from None
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
    except identity_service.NotGroupOwnerError as exc:
        raise _not_group_owner(exc) from None
    except member_service.InviteNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _INVITE_NOT_FOUND) from None


@router.post(
    "/pet-invites",
    response_model=InviteBundleCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_invite_bundle(
    body: InviteBundleCreate, user: CurrentAppUser, session: Session
) -> InviteBundleCreated:
    """**강아지 여러 마리를 토큰 하나에** (MVP 결정 §2). 평문 토큰은 이 응답에만 있습니다.

    구 경로 `POST /app/pets/{pet_id}/invites` 는 그대로 살아 있습니다 — 그쪽은 한 마리
    묶음을 만들고 같은 규칙을 지납니다. 경로를 새로 낸 이유는 `{pet_id}` 가 URL 에 있는데
    묶음이 여러 마리면 뜻이 모순되고, **구 서버가 새 body 를 조용히 무시**하기 때문입니다.

    강아지마다 **그룹 주보호자**여야 합니다 — 하나라도 아니면 409 이고 아무것도 안 만듭니다.
    """
    try:
        invite, token, pet_ids = await member_service.create_invite_bundle(
            session, user.app_user_id, body.pet_ids
        )
    except PetNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _PET_NOT_FOUND) from None
    except identity_service.NotGroupOwnerError as exc:
        raise _not_group_owner(exc) from None
    except member_service.PetFarewelledError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"배웅한 아이는 초대할 수 없어요 ({exc.pet_name}).",
        ) from None
    except member_service.InviteLimitError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"살아 있는 초대는 {member_service.MAX_ACTIVE_INVITES}개까지입니다.",
        ) from None
    return InviteBundleCreated(
        id=invite.id, pet_ids=pet_ids, token=token, expires_at=invite.expires_at
    )


@router.get("/pet-invites", response_model=InviteBundleListResponse)
async def list_invite_bundles(
    user: CurrentAppUser, session: Session
) -> InviteBundleListResponse:
    """**내가 보낸 묶음 전부.** 강아지별 목록(구 경로)과 달리 왕복이 한 번입니다.

    담긴 강아지 이름까지 실어 앱이 "맥스·코코를 부른 링크" 로 그릴 수 있게 합니다.
    **토큰도 해시도 안 돌려줍니다** — 서버는 해시만 들고 있습니다.
    """
    invites, bundles, names = await member_service.list_my_invites(session, user.app_user_id)
    return InviteBundleListResponse(
        invites=[
            InviteBundleOut(
                id=invite.id,
                pets=[
                    InvitePetBrief(pet_id=pet_id, name=names.get(pet_id, ""))
                    for pet_id in bundles.get(invite.id, [])
                ],
                expires_at=invite.expires_at,
                created_at=invite.created_at,
                accepted_at=invite.accepted_at,
            )
            for invite in invites
        ]
    )


@router.delete("/pet-invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_invite_bundle(
    invite_id: uuid.UUID, user: CurrentAppUser, session: Session
) -> None:
    """묶음 **전체** 취소. **초대한 사람만** — 남의 초대 id 를 넣어도 404 입니다."""
    try:
        await member_service.cancel_invite_bundle(session, user.app_user_id, invite_id)
    except member_service.InviteNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _INVITE_NOT_FOUND) from None


@router.post("/pet-invites/preview", response_model=InvitePreviewResponse)
async def preview_invite(
    body: InvitePreviewRequest, user: CurrentAppUser, session: Session
) -> InvitePreviewResponse:
    """수락 전 미리보기 + **연결 후보** (MVP 결정 §8).

    **GET 이 아니라 POST 인 이유** — 토큰을 URL 에 실으면 nginx access log·Referer·브라우저
    히스토리에 평문이 남습니다. 수락이 body 로 받는 것과 같은 자리입니다.

    응답에 **건강정보가 없습니다** — 아직 구성원이 아닌 사람에게 지병·복약을 내보이면
    토큰 하나로 남의 집 의료 정보를 읽는 자리가 됩니다.

    **새 앱은 이 요청이 성공한 뒤에만 새 수락 계약을 씁니다** (MVP 결정 §9). 구 서버에는
    이 경로가 없어 404 인데, 그때 `links` 를 실어 보내면 구 서버가 그것을 **조용히 무시하고
    200** 을 냅니다 — 사용자가 연결을 골랐는데 연결 없이 참여됩니다.
    """
    try:
        preview = await member_service.preview_invite(session, user.app_user_id, body.token)
    except member_service.InviteNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _INVITE_NOT_FOUND) from None
    except member_service.InviteExpiredError:
        raise HTTPException(status.HTTP_410_GONE, _INVITE_EXPIRED) from None
    except member_service.InviteBundleChangedError:
        raise HTTPException(status.HTTP_410_GONE, _INVITE_BUNDLE_CHANGED) from None
    return InvitePreviewResponse(
        invited_by_nickname=preview.invited_by_nickname,
        expires_at=preview.expires_at,
        pets=[
            InvitePreviewPet(**_brief(pet, already_member=already_member))
            for pet, already_member in preview.pets
        ],
        link_candidates=[InvitePetBrief(**_brief(pet)) for pet in preview.link_candidates],
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
