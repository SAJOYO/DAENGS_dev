"""walks 조회·저장. 쿼리만 있고 판단은 없습니다.

"이미 올라온 산책인가"를 어떻게 다룰지는 services 가 정합니다.
commit 도 하지 않습니다 — 트랜잭션 경계는 services 가 잡습니다.
"""

import uuid
from datetime import datetime

from sqlalchemy import delete, exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, undefer

from daengs_backend.models import Walk, WalkAnalysis, WalkPet, WalkPointChunk

__all__ = [
    "add",
    "add_analysis",
    "delete_all_for_owner",
    "delete_walks_only_with",
    "existing_chunk_starts",
    "get_analysis_for_input",
    "get_by_client_session",
    "get_owned",
    "get_owned_for_update",
    "is_client_session_conflict",
    "list_for_owner",
]


async def list_for_owner(session: AsyncSession, app_user_id: uuid.UUID) -> list[Walk]:
    """내 산책 전부, **최근 순.**

    좌표는 안 붙입니다 — 목록에 좌표까지 실으면 스무 건에 수만 점이 딸려 옵니다.
    나간 아이들(`pets`)은 붙입니다. 산책당 많아야 몇 줄이고, 안 붙이면 응답을 만들다
    지연 로딩에서 터집니다.

    `started_at` 이 같을 수 있어 `id` 로 한 번 더 정렬합니다.
    """
    stmt = (
        select(Walk)
        .where(Walk.app_user_id == app_user_id)
        .options(selectinload(Walk.pets))
        .order_by(Walk.started_at.desc(), Walk.id)
    )
    return list(await session.scalars(stmt))


async def get_owned(
    session: AsyncSession, app_user_id: uuid.UUID, walk_id: uuid.UUID
) -> Walk | None:
    """**내 것일 때만** 좌표까지 붙여서 돌려줍니다.

    소유자 조건을 이 함수 안에 묶어 두면 부르는 쪽이 잊을 자리가 없습니다
    (`repositories/pet.py` 와 같은 이유).

    `selectinload` 로 좌표와 나간 아이들을 같이 읽습니다. 지연 로딩이면 비동기
    세션에서 접근하는 순간 터집니다.
    """
    stmt = (
        select(Walk)
        .where(Walk.id == walk_id, Walk.app_user_id == app_user_id)
        .options(selectinload(Walk.points), selectinload(Walk.pets))
    )
    return await session.scalar(stmt)


async def get_owned_for_update(
    session: AsyncSession, app_user_id: uuid.UUID, walk_id: uuid.UUID
) -> Walk | None:
    """finalize·append가 공유하는 산책 행 잠금 조회.

    ``analysis_state``는 수동 migration 전 기존 조회를 보호하려고 deferred로
    매핑했다. 상태 전이를 하는 이 조회에서만 명시적으로 같이 읽는다.
    """
    stmt = (
        select(Walk)
        .where(Walk.id == walk_id, Walk.app_user_id == app_user_id)
        .options(
            undefer(Walk.analysis_state),
            selectinload(Walk.points),
            selectinload(Walk.pets),
        )
        .with_for_update()
    )
    return await session.scalar(stmt)


async def get_by_client_session(
    session: AsyncSession, app_user_id: uuid.UUID, client_session_id: uuid.UUID
) -> Walk | None:
    """기기가 준 id 로 찾습니다. **재시도를 안전하게 만드는 조회**입니다.

    앱은 네트워크가 끊기면 다음에 다시 올립니다. 그때 이미 올라온 것이면 새로 넣지
    않고 있던 것을 돌려줘야 같은 산책이 두 건이 되지 않습니다.
    """
    stmt = (
        select(Walk)
        .where(
            Walk.app_user_id == app_user_id,
            Walk.client_session_id == client_session_id,
        )
        # 찾는 목적은 "이미 올라왔나"지만, 호출자는 기존 Walk를 곧바로 **좌표 포함
        # 상세 응답**으로 돌려줍니다. 둘을 미리 읽지 않으면 async 세션의 응답 직렬화
        # 단계에서 lazy load가 발생해 MissingGreenlet 500이 납니다.
        .options(selectinload(Walk.points), selectinload(Walk.pets))
    )
    return await session.scalar(stmt)


async def count_for_pet_between(
    session: AsyncSession,
    _app_user_id: uuid.UUID,
    pet_id: uuid.UUID,
    start: datetime,
    end: datetime,
) -> int:
    """그 아이가 나간 산책 수 — `started_at` 이 `[start, end)` 인 것. 케어 로그의 하루 요약이 씁니다 (#332).

    산책을 케어 이벤트로 다시 적지 않고 **여기서 센다** — `walks` 가 진실이고, 한 사실이 두 곳에
    있으면 반드시 어긋납니다.

    **소유자 조건을 걸지 않습니다** (docs/co-care.md §2). 부르는 쪽(`care_event.day_summary`)이
    이미 강아지 접근 권한을 확인한 뒤라, 여기서 다시 사람으로 거르면 **다른 보호자의 산책만
    빠집니다** — 아빠가 아침에 다녀온 산책이 내 오늘 요약에서 사라집니다. `walk_pets` 조인이
    "그 아이가 나간 산책" 을 정확히 집으니 사람 조건은 필요 없습니다. 인자는 부르는 쪽을
    안 고치려고 시그니처에만 남겨 둡니다.

    **산책의 소유는 그대로 사람 것입니다** — 여기서 여는 것은 세는 것뿐이고, 목록·수정은
    `walks.app_user_id` 를 계속 봅니다.
    """
    stmt = (
        select(func.count(func.distinct(Walk.id)))
        .join(WalkPet, WalkPet.walk_id == Walk.id)
        .where(
            WalkPet.pet_id == pet_id,
            Walk.started_at >= start,
            Walk.started_at < end,
        )
    )
    return int(await session.scalar(stmt) or 0)


async def delete_walks_only_with(session: AsyncSession, pet_id: uuid.UUID) -> int:
    """**그 아이와만** 나간 산책을 지웁니다.

    강아지를 지울 때 부릅니다. 다른 아이와 같이 나간 산책은 **남깁니다** — 그 산책은
    남은 아이의 기록이기도 해서, 지우면 그 아이의 운동량이 통째로 빕니다. 그 산책에서
    이 아이만 빠지는 것은 조인 행의 `ON DELETE CASCADE` 가 알아서 합니다.

    아무도 안 붙은 산책은 애초에 조인 행이 없어 여기 걸리지 않습니다.

    :returns: 지운 산책 수.
    """
    others = WalkPet.__table__.alias("others")
    solo = select(WalkPet.walk_id).where(
        WalkPet.pet_id == pet_id,
        ~exists().where(
            others.c.walk_id == WalkPet.walk_id,
            others.c.pet_id != pet_id,
        ),
    )
    result = await session.execute(delete(Walk).where(Walk.id.in_(solo)))
    return result.rowcount or 0


async def delete_all_for_owner(session: AsyncSession, app_user_id: uuid.UUID) -> int:
    """탈퇴한 회원의 산책을 전부 지웁니다.

    ``walk_point_chunks``(또는 아직 이관 전 DB의 ``walk_points``)와 ``walk_pets``는
    모두 ``walks.id ON DELETE CASCADE``라 이 DELETE 한 번에 같이 없어집니다.
    """
    result = await session.execute(delete(Walk).where(Walk.app_user_id == app_user_id))
    return result.rowcount or 0


def add(session: AsyncSession, walk: Walk) -> Walk:
    session.add(walk)
    return walk


def is_client_session_conflict(error: IntegrityError) -> bool:
    """asyncpg가 보고한 이 멱등 키의 유니크 충돌만 식별합니다.

    SQLAlchemy의 DBAPI 어댑터가 원래 asyncpg 예외를 cause로 보존합니다.
    오류 메시지 문자열 대신 SQLSTATE와 실제 제약 이름을 함께 확인합니다.
    """
    driver_error = error.orig.__cause__
    return (
        getattr(driver_error, "sqlstate", None) == "23505"
        and getattr(driver_error, "constraint_name", None) == "walks_client_session_unique"
    )


def add_analysis(session: AsyncSession, analysis: WalkAnalysis) -> WalkAnalysis:
    session.add(analysis)
    return analysis


async def get_analysis_for_input(
    session: AsyncSession,
    *,
    walk_id: uuid.UUID,
    input_fingerprint: str,
) -> WalkAnalysis | None:
    """같은 봉인 입력에서 처음 만든 분석.

    계산 세대가 나중에 추가되어도 원래 finalize 재시도는 처음 응답과
    같은 analysis_id를 돌려줘야 한다.
    """
    stmt = (
        select(WalkAnalysis)
        .where(
            WalkAnalysis.walk_id == walk_id,
            WalkAnalysis.input_fingerprint == input_fingerprint,
        )
        .options(selectinload(WalkAnalysis.capsule))
        .order_by(WalkAnalysis.derived_at, WalkAnalysis.id)
        .limit(1)
    )
    return await session.scalar(stmt)


async def existing_chunk_starts(session: AsyncSession, walk_id: uuid.UUID) -> set[int]:
    """이미 저장된 묶음의 첫 순번.

    나눠 올릴 때 **같은 묶음이 두 번 와도** 조용히 넘기려고 씁니다. DB 의 PK 가
    막아 주기는 하지만, 그건 예외로 터지는 방식이라 재시도가 500 이 됩니다.

    예전에는 좌표 순번을 전부 읽었습니다(`existing_seqs`). 30분 산책이면 1,842개를
    읽어 집합으로 만들었는데, **묶음 단위로 판정하면 몇 개면 됩니다.** payload 를
    풀지 않는 것도 같은 이유입니다.
    """
    stmt = select(WalkPointChunk.seq_from).where(WalkPointChunk.walk_id == walk_id)
    return set(await session.scalars(stmt))
