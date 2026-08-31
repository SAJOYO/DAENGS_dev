"""강아지 프로필의 규칙. 트랜잭션 경계도 여기입니다.

라우터는 HTTP 만 보고, 리포지토리는 쿼리만 합니다. "몇 마리까지"·"대표를 누구로"
같은 판단은 전부 여기 모입니다.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import AppUser, Pet
from daengs_backend.repositories import app_user as app_user_repo
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.schemas.pet import PetUpsert

#: 한 계정에 등록할 수 있는 마릿수.
#:
#: ⚠️ **임의값입니다.** 앱 쪽에서 정한 기준은 "몇 마리가 자연스러운가"가 아니라
#: **미니룸에 몇 마리까지 담기나** 입니다 — 등록한 강아지가 방을 돌아다니게 할
#: 계획이라, 12×12 격자에 가구가 차 있고 강아지 간격이 1.3칸인 것이 상한을 정합니다.
#: 실기기에서 세워 보고 정하기로 했고, 그 결과를 여기 한 줄만 고치면 됩니다.
MAX_PETS_PER_USER = 5


class PetLimitReachedError(Exception):
    """마릿수 상한. 라우터가 409 로 바꿉니다."""


class PetNotFoundError(Exception):
    """내 강아지가 아니거나 없습니다.

    **남의 것일 때도 이 예외입니다.** 403 으로 나누면 "그 id 는 존재한다"를
    알려 주는 셈이라, 없는 것과 남의 것을 같은 404 로 뭉갭니다.
    """


async def list_pets(session: AsyncSession, app_user_id: uuid.UUID) -> tuple[list[Pet], uuid.UUID | None]:
    """내 강아지와 대표 id. 대표는 계정 쪽에 있어서 같이 읽어 옵니다."""
    pets = await pet_repo.list_for_owner(session, app_user_id)
    user = await app_user_repo.get_by_id(session, app_user_id)
    return pets, (user.primary_pet_id if user else None)


async def create_pet(
    session: AsyncSession, app_user_id: uuid.UUID, body: PetUpsert
) -> tuple[Pet, uuid.UUID | None]:
    """등록. **첫 아이는 자동으로 대표가 됩니다.**

    고르라고 묻지 않는 이유는 고를 것이 없어서입니다. 두 마리째부터 사용자가 정합니다.

    대표 id 를 같이 돌려주는 이유는 라우터가 응답을 만들 때 필요해서입니다 —
    안 주면 라우터가 목록을 한 번 더 읽어야 합니다.
    """
    if await pet_repo.count_for_owner(session, app_user_id) >= MAX_PETS_PER_USER:
        raise PetLimitReachedError

    pet = Pet(app_user_id=app_user_id, **body.model_dump())
    pet_repo.add(session, pet)
    # id 가 있어야 대표로 세울 수 있습니다. commit 전에 DB 기본값을 받아 옵니다.
    await session.flush()

    user = await app_user_repo.get_by_id(session, app_user_id)
    if user is not None and user.primary_pet_id is None:
        user.primary_pet_id = pet.id

    await session.commit()
    # refresh 하지 않습니다 — 세션이 `expire_on_commit=False` 라 commit 뒤에도
    # 속성을 그대로 읽습니다 (core/database.py). 부르면 왕복만 한 번 더 늡니다.
    return pet, (user.primary_pet_id if user is not None else None)


async def update_pet(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID, body: PetUpsert
) -> Pet:
    pet = await pet_repo.get_owned(session, app_user_id, pet_id)
    if pet is None:
        raise PetNotFoundError

    for field, value in body.model_dump().items():
        setattr(pet, field, value)

    await session.commit()
    return pet


async def delete_pet(session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID) -> None:
    """삭제. **대표를 지우면 남은 아이 중 먼저 등록한 아이가 승계합니다.**

    "대표가 없는 상태"를 안 만들면 화면이 단순해집니다 — 앱이 매번 "대표가 없으면"
    을 다루지 않아도 됩니다. 마지막 한 마리를 지우면 그때만 대표가 없습니다.

    FK 가 `ON DELETE SET NULL` 이라 지우면 `primary_pet_id` 는 저절로 비지만,
    **누구를 대신 세울지는 정책이라 DB 가 못 정합니다.**
    """
    pet = await pet_repo.get_owned(session, app_user_id, pet_id)
    if pet is None:
        raise PetNotFoundError

    user = await app_user_repo.get_by_id(session, app_user_id)
    was_primary = user is not None and user.primary_pet_id == pet.id

    await pet_repo.delete(session, pet)
    await session.flush()

    if was_primary and user is not None:
        remaining = await pet_repo.list_for_owner(session, app_user_id)
        user.primary_pet_id = remaining[0].id if remaining else None

    await session.commit()


async def set_primary(session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID) -> AppUser:
    """대표를 바꿉니다. **내 강아지인지 여기서 확인합니다.**

    FK 는 "존재하는 pets 행"까지만 보장하고 그게 내 것인지는 안 봅니다
    (05_pets.sql 주석). 그래서 이 검사를 빠뜨리면 남의 강아지를 내 대표로 세울 수
    있습니다.
    """
    pet = await pet_repo.get_owned(session, app_user_id, pet_id)
    if pet is None:
        raise PetNotFoundError

    user = await app_user_repo.get_by_id(session, app_user_id)
    if user is None:
        raise PetNotFoundError
    user.primary_pet_id = pet.id
    await session.commit()
    return user
