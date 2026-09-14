"""산책 기록 **공동 조회** — 누가 어느 강아지의 산책 목록·상세·경로를 볼 수 있나.

MVP 결정 §7·§11 P0-11 의 "산책 기록·경로 공동 조회와 수행자 표시" 가운데 **구현이 빠져 있던
부분을 보완**합니다 (docs/co-care.md 「산책 기록 공동 조회」).

⚠️ **조회만 엽니다.** 산책의 소유(`walks.app_user_id`)·좌표 추가·봉인·기록 보정은 계속
`walk.get_owned`(올린 사람)입니다. `pet_repo.member_condition` 도 넓히지 않습니다 — 그것을
넓히면 보행·스크리닝·대화·점령까지 조용히 공유됩니다.

⚠️ **게임·점령 결과는 싣지 않습니다** — 이번 결정으로 후순위 보류입니다.

**그룹 확장은 보수적으로 합니다.** 연결된 강아지에서 요청자가 그룹 안 어느 행의 **대표**이거나
그룹 주보호자 행(앵커)의 **구성원**일 때만 그룹 전체로 넓힙니다. 연결된 개인 행에만 돌보미로
있던 사람(연결 전 그 행을 돌보던 사람)은 자기가 돌보는 행의 산책만 봅니다 — 그 사람의 그룹
접근 범위는 아직 결정되지 않았으므로(#538 권한 검토), 이 기능이 그 결론을 앞서 넓히지 않습니다.
"""

import base64
import binascii
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import Walk
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.repositories import pet_identity as identity_repo
from daengs_backend.repositories import pet_member as member_repo
from daengs_backend.repositories import walk_group as walk_group_repo
from daengs_backend.services import pet_identity as identity_service
from daengs_backend.services import pet_member as member_service

__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "GroupWalk",
    "GroupWalkPage",
    "InvalidCursorError",
    "PetNotReadableError",
    "WalkNotReadableError",
    "get_detail",
    "list_page",
    "readable_pet_ids",
]

DEFAULT_LIMIT = 20
MAX_LIMIT = 50


class PetNotReadableError(Exception):
    """그 강아지의 구성원이 아닙니다. 라우터가 404 로 바꿉니다."""


class WalkNotReadableError(Exception):
    """볼 수 있는 강아지에 태그된 산책이 아닙니다. 라우터가 404 로 바꿉니다."""


class InvalidCursorError(ValueError):
    """커서를 읽을 수 없습니다. 라우터가 422 로 바꿉니다."""


@dataclass(frozen=True)
class GroupWalk:
    walk: Walk
    #: 요청자가 볼 수 있는 강아지만 남긴 태그. 같은 산책에 함께 태그된 그룹 밖 강아지는 뺍니다.
    pet_ids: list[uuid.UUID]
    #: 수행자 닉네임. **지금도 그 그룹의 구성원일 때만** 옵니다 (`group_actor_label`).
    actor_nickname: str | None
    is_mine: bool
    distance_m: int | None
    moving_s: int | None


@dataclass(frozen=True)
class GroupWalkPage:
    walks: list[GroupWalk]
    next_cursor: str | None


async def readable_pet_ids(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID
) -> list[uuid.UUID]:
    """요청자가 이 강아지로 볼 수 있는 산책의 pet id 묶음.

    1. 그 행의 구성원이 아니면 404 (`get_accessible`).
    2. 연결 안 됐으면 그 행 하나.
    3. 연결됐으면 그룹 안 어느 행의 대표이거나 앵커 행의 구성원일 때만 그룹 전체
       (`group_pet_ids_of`), 아니면 그 행 하나.

    앱이 보낸 id 목록을 받는 자리를 만들지 않습니다 — 묶음은 늘 서버가 정합니다.
    나가기·내보내기는 멤버십과 연결을 한 트랜잭션에서 지우므로(`remove_member` → `detach_user`)
    그 뒤로는 1·3 에서 곧바로 좁혀지거나 404 입니다.
    """
    pet = await pet_repo.get_accessible(session, app_user_id, pet_id)
    if pet is None:
        raise PetNotReadableError
    if pet.identity_id is None:
        return [pet.id]
    rows = await identity_repo.pets_for(session, pet.identity_id)
    owns_a_row = any(row.app_user_id == app_user_id for row in rows)
    if not owns_a_row:
        common = await identity_service.common_of(session, pet)
        if not await member_repo.is_member(session, common.id, app_user_id):
            return [pet.id]
    return await identity_service.group_pet_ids_of(session, pet)


def encode_cursor(walk: Walk) -> str:
    raw = f"{walk.started_at.isoformat()}|{walk.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        started, walk_id = base64.urlsafe_b64decode(padded.encode()).decode().split("|")
        at = datetime.fromisoformat(started)
        if at.tzinfo is None:
            raise ValueError("naive cursor")
        return at, uuid.UUID(walk_id)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise InvalidCursorError from exc


async def _decorate(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    group_ids: list[uuid.UUID],
    walks: list[Walk],
) -> list[GroupWalk]:
    numbers = await walk_group_repo.latest_numbers(session, [w.id for w in walks])
    # 사람 수만큼만 묻습니다 — 같은 사람이 여러 산책을 올린 경우가 흔합니다.
    labels = {
        uid: await member_service.group_actor_label(session, group_ids, uid)
        for uid in {w.app_user_id for w in walks}
    }
    allowed = set(group_ids)
    decorated = []
    for walk in walks:
        n = numbers.get(walk.id)
        decorated.append(
            GroupWalk(
                walk=walk,
                pet_ids=[pid for pid in walk.pet_ids if pid in allowed],
                actor_nickname=labels.get(walk.app_user_id),
                is_mine=walk.app_user_id == app_user_id,
                distance_m=n.moving_distance_m if n else None,
                moving_s=n.moving_s if n else None,
            )
        )
    return decorated


async def list_page(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    pet_id: uuid.UUID,
    *,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> GroupWalkPage:
    """그 강아지의 산책 한 페이지, 최근 순. **권한을 먼저 보고** 커서를 읽습니다 — 거꾸로 하면
    남의 강아지 id 에 잘못된 커서를 넣었을 때 404 대신 422 가 나가 존재 여부가 샙니다."""
    group_ids = await readable_pet_ids(session, app_user_id, pet_id)
    before = decode_cursor(cursor) if cursor else None
    limit = max(1, min(limit, MAX_LIMIT))
    rows = await walk_group_repo.list_group_page(session, group_ids, limit=limit + 1, before=before)
    page = rows[:limit]
    return GroupWalkPage(
        walks=await _decorate(session, app_user_id, group_ids, page),
        next_cursor=encode_cursor(page[-1]) if len(rows) > limit else None,
    )


async def get_detail(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID, walk_id: uuid.UUID
) -> GroupWalk:
    """한 건과 그 경로. 볼 수 있는 강아지에 태그된 산책이 아니면 404 입니다."""
    group_ids = await readable_pet_ids(session, app_user_id, pet_id)
    walk = await walk_group_repo.get_in_group(session, group_ids, walk_id)
    if walk is None:
        raise WalkNotReadableError
    return (await _decorate(session, app_user_id, group_ids, [walk]))[0]
