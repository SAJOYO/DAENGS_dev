"""반려견 프로필 HTTP 경계 — 서비스의 예외를 상태 코드로 바꿉니다.

판단은 여기 없습니다. "이 개를 이 회원이 봐도 되나"는 services/dog.py 가 정하고,
여기는 그 예외를 404 로 옮길 뿐입니다 (타 회원의 개 = 없는 개 — 존재를 숨깁니다).

앱 회원 전용입니다. 관리자 토큰은 `CurrentAppUser` 가 401 로 끊습니다 — 관리자
콘솔에서 회원의 강아지를 봐야 하는 날이 오면 별도 관리자 라우터로 만드세요
(`current_app_user` 를 넓히면 이 라우터가 통째로 열립니다 — deps.py 주석).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.schemas.dog import DogCreateRequest, DogResponse
from daengs_backend.services import dog as dog_service

router = APIRouter(prefix="/dogs", tags=["dogs"])

Session = Annotated[AsyncSession, Depends(get_session)]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=DogResponse)
async def register_dog(
    body: DogCreateRequest, principal: CurrentAppUser, session: Session
) -> DogResponse:
    dog = await dog_service.register(session, principal.app_user_id, body)
    return DogResponse.model_validate(dog)


@router.get("", response_model=list[DogResponse])
async def list_my_dogs(principal: CurrentAppUser, session: Session) -> list[DogResponse]:
    dogs = await dog_service.list_mine(session, principal.app_user_id)
    return [DogResponse.model_validate(dog) for dog in dogs]


@router.get("/{dog_id}", response_model=DogResponse)
async def get_my_dog(
    dog_id: uuid.UUID, principal: CurrentAppUser, session: Session
) -> DogResponse:
    try:
        dog = await dog_service.get_mine(session, principal.app_user_id, dog_id)
    except dog_service.DogNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "강아지를 찾을 수 없습니다.") from None
    return DogResponse.model_validate(dog)
