"""반려견 프로필 유스케이스 — 소유권 판단과 트랜잭션 경계.

**타 회원의 개는 "없는 개"와 같은 예외입니다.** 403 으로 가르면 "그 id 의 개가
존재한다"는 사실 자체가 새어 나갑니다 — dog id 는 추측 가능한 입력이므로 존재를
숨깁니다. 이 판단을 바꾸려면 라우터가 아니라 여기를 고치세요 (판단은 한 곳에).
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import Dog
from daengs_backend.repositories import dog as dog_repo
from daengs_backend.schemas.dog import DogCreateRequest

__all__ = ["DogNotFoundError", "get_mine", "list_mine", "register"]


class DogNotFoundError(Exception):
    """없는 개 — 타 회원의 개도 여기 포함됩니다 (모듈 docstring)."""


async def register(
    session: AsyncSession, app_user_id: uuid.UUID, request: DogCreateRequest
) -> Dog:
    """이 회원 소유로 강아지 한 마리를 등록합니다. 여기서 commit 합니다."""
    dog = await dog_repo.create(
        session,
        app_user_id=app_user_id,
        name=request.name,
        breed=request.breed,
        birth_date=request.birth_date,
        sex=request.sex,
        neutered=request.neutered,
        weight_kg=request.weight_kg,
        size_class=request.size_class,
    )
    await session.commit()
    return dog


async def list_mine(session: AsyncSession, app_user_id: uuid.UUID) -> list[Dog]:
    """내 강아지 전부. 빈 목록은 정상 상태입니다 — 아직 등록 전일 뿐."""
    return await dog_repo.list_by_owner(session, app_user_id)


async def get_mine(
    session: AsyncSession, app_user_id: uuid.UUID, dog_id: uuid.UUID
) -> Dog:
    """내 강아지 한 마리. 없거나 남의 개면 DogNotFoundError."""
    dog = await dog_repo.get_by_id(session, dog_id)
    if dog is None or dog.app_user_id != app_user_id:
        raise DogNotFoundError
    return dog
