"""강아지 프로필 HTTP 경계 — 서비스의 예외를 상태 코드로 바꿉니다.

판단은 여기 없습니다. "몇 마리까지"·"대표를 누구로"는 services/pet.py 가 정합니다.

**경로가 `/app/pets` 인 이유**는 앱 회원 전용이기 때문입니다. 관리자 콘솔이
나중에 회원의 강아지를 볼 일이 생기면 `/admin/...` 아래에 따로 둡니다 —
같은 경로가 주체에 따라 다르게 동작하면 한 글자 차이로 엉뚱한 쪽을 부릅니다
(`/auth/app/*` 을 나눈 것과 같은 이유).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.models import Pet
from daengs_backend.schemas.pet import (
    PetListResponse,
    PetResponse,
    PetUpsert,
    PrimaryPetRequest,
)
from daengs_backend.services import pet as pet_service

router = APIRouter(prefix="/app/pets", tags=["pets"])


def _to_response(pet: Pet, primary_pet_id: uuid.UUID | None) -> PetResponse:
    return PetResponse(
        id=pet.id,
        name=pet.name,
        breed=pet.breed,
        sex=pet.sex,
        neutered=pet.neutered,
        weight_kg=pet.weight_kg,
        birth_date=pet.birth_date,
        birth_date_kind=pet.birth_date_kind,
        farewell_on=pet.farewell_on,
        is_primary=pet.id == primary_pet_id,
    )


@router.get("", response_model=PetListResponse)
async def list_pets(
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PetListResponse:
    """내 강아지 전부. 등록 순서대로입니다.

    `max_pets` 를 같이 보내는 이유는 앱이 `+` 버튼을 언제 감출지 정하기 때문입니다.
    앱에 숫자를 박아 두면 서버가 상한을 바꿀 때 갈라집니다.
    """
    pets, primary_id = await pet_service.list_pets(session, user.app_user_id)
    return PetListResponse(
        pets=[_to_response(p, primary_id) for p in pets],
        max_pets=pet_service.MAX_PETS_PER_USER,
    )


@router.post("", response_model=PetResponse, status_code=status.HTTP_201_CREATED)
async def create_pet(
    body: PetUpsert,
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PetResponse:
    """등록합니다. 첫 아이는 자동으로 대표가 됩니다."""
    try:
        pet, primary_id = await pet_service.create_pet(session, user.app_user_id, body)
    except pet_service.PetLimitReachedError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"강아지는 {pet_service.MAX_PETS_PER_USER}마리까지 등록할 수 있습니다.",
        ) from None
    return _to_response(pet, primary_id)


# ⚠️ **`/{pet_id}` 보다 먼저 선언해야 한다.** FastAPI 는 등록 순서대로 매칭하므로,
# 뒤에 두면 `/app/pets/primary` 가 `update_pet` 으로 가서 "primary" 를 UUID 로
# 파싱하려다 422 가 난다. 실제로 그렇게 만들었다가 라우트 매칭을 재서 찾았다.
@router.put("/primary", status_code=status.HTTP_204_NO_CONTENT)
async def set_primary(
    body: PrimaryPetRequest,
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """대표를 바꿉니다. 내 강아지가 아니면 404 입니다."""
    try:
        await pet_service.set_primary(session, user.app_user_id, body.pet_id)
    except pet_service.PetNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "강아지를 찾을 수 없습니다.") from None


@router.put("/{pet_id}", response_model=PetResponse)
async def update_pet(
    pet_id: uuid.UUID,
    body: PetUpsert,
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PetResponse:
    """전체를 다시 보냅니다(PUT).

    부분 수정을 안 두는 이유는 `None` 의 뜻이 갈리기 때문입니다 (schemas/pet.py).
    """
    try:
        pet = await pet_service.update_pet(session, user.app_user_id, pet_id, body)
    except pet_service.PetNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "강아지를 찾을 수 없습니다.") from None
    _, primary_id = await pet_service.list_pets(session, user.app_user_id)
    return _to_response(pet, primary_id)


@router.delete("/{pet_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pet(
    pet_id: uuid.UUID,
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """지웁니다. 대표를 지우면 남은 아이 중 먼저 등록한 아이가 승계합니다."""
    try:
        await pet_service.delete_pet(session, user.app_user_id, pet_id)
    except pet_service.PetNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "강아지를 찾을 수 없습니다.") from None
