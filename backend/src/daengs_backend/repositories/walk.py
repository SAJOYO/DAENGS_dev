"""walks 조회·저장. 쿼리만 있고 판단은 없습니다.

"이미 올라온 산책인가"를 어떻게 다룰지는 services 가 정합니다.
commit 도 하지 않습니다 — 트랜잭션 경계는 services 가 잡습니다.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, undefer

from daengs_backend.models import ActivityWalkHead, Walk, WalkAnalysis, WalkPet, WalkPointChunk

__all__ = [
    "WalkActivitySums",
    "activity_for_pet_between",
    "add",
    "add_analysis",
    "delete_all_for_owner",
    "delete_walks_only_with",
    "existing_chunk_starts",
    "get_analysis_for_input",
    "get_by_client_session",
    "get_owned",
    "get_owned_for_update",
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


@dataclass(frozen=True)
class WalkActivitySums:
    """`activity_for_pet_between` 이 돌려주는 것. 건수와 합계가 **따로**인 이유는
    `WalkActivityContext` 독스트링에 있다."""

    walk_count: int
    measured_walk_count: int
    distance_m: int
    moving_s: int
    last_started_at: datetime | None


def _walked_stmt(pet_id: uuid.UUID, start: datetime, end: datetime):
    """건수·마지막 시작 시각 — head 를 거치지 않는다(측정 여부와 무관하게 전부 센다).

    모듈 수준 함수로 꺼낸 이유는 테스트가 조인 사슬을 컴파일된 SQL 로 직접 보기
    위해서다(`tests/test_assistant_walk_activity.py`) — `activity_for_pet_between` 의
    지역 변수였을 때는 그 검증에 손이 안 닿았다.
    """
    return (
        select(
            # `distinct` 는 순수 방어다. `walk_pets` 의 PK 가 (walk_id, pet_id) 복합이라
            # 이 조인에서 한 산책이 두 번 나오는 fan-out 은 구조적으로 불가능하다 —
            # 지워도 결과는 같지만, 나중에 이 쿼리에 조인이 더 붙을 때의 안전판으로 둔다.
            func.count(func.distinct(Walk.id)).label("walk_count"),
            func.max(Walk.started_at).label("last_started_at"),
        )
        .join(WalkPet, WalkPet.walk_id == Walk.id)
        .where(
            WalkPet.pet_id == pet_id,
            Walk.started_at >= start,
            Walk.started_at < end,
        )
    )


def _measured_stmt(pet_id: uuid.UUID, start: datetime, end: datetime):
    """측정 건수·합계 거리·합계 이동 시간 — **반드시 `activity_walk_heads` 를 경유한다.**

    `walk_analyses` 에 `Walk` 를 직접(`WalkAnalysis.walk_id == Walk.id`) 잇지 않는다 —
    그 표는 한 산책에 여러 계산 세대가 쌓이는 표라(유니크 제약이 6칸), 직결하면 거리가
    세대 수만큼 불어난다. head 는 `walk_id` 가 PK 라 산책당 정확히 한 행을 가리키므로,
    `walks → walk_pets → activity_walk_heads → walk_analyses` 로만 잇는다.

    `_walked_stmt` 와 `WHERE` 절이 **동일해야 한다** — 다르면 이 함수가 센 건수가
    `_walked_stmt` 보다 커질 수 있고, `WalkActivityContext` 의
    `measured_walk_count <= walk_count` 검증이 런타임에 터진다.
    """
    return (
        select(
            # 위와 같은 이유로 순수 방어다(`walk_pets` PK 복합 + head 의 walk_id 단일 PK).
            func.count(func.distinct(Walk.id)).label("measured_walk_count"),
            func.coalesce(func.sum(WalkAnalysis.moving_distance_m), 0).label("distance_m"),
            func.coalesce(func.sum(WalkAnalysis.moving_s), 0).label("moving_s"),
        )
        .join(WalkPet, WalkPet.walk_id == Walk.id)
        .join(ActivityWalkHead, ActivityWalkHead.walk_id == Walk.id)
        .join(WalkAnalysis, WalkAnalysis.id == ActivityWalkHead.analysis_id)
        .where(
            WalkPet.pet_id == pet_id,
            Walk.started_at >= start,
            Walk.started_at < end,
        )
    )


async def activity_for_pet_between(
    session: AsyncSession,
    pet_id: uuid.UUID,
    start: datetime,
    end: datetime,
) -> WalkActivitySums:
    """그 아이의 산책 건수와, **측정이 끝난 것만의** 합계 거리·이동 시간 (D-072).

    **소유자 조건을 안 겁니다** — `count_for_pet_between` 과 같은 이유입니다(docs/co-care.md
    §2). 부르는 쪽이 이미 접근 권한을 확인했고, 여기서 사람으로 다시 거르면 다른 보호자가
    다녀온 산책만 빠집니다.

    **`activity_walk_heads` 를 경유하는 것이 이 함수의 전부입니다.** `walk_analyses` 는 한
    산책에 여러 세대가 쌓이는 표라(유니크 제약이 6칸), 거기 바로 `SUM` 을 걸면 거리가
    배로 불어납니다. head 는 `walk_id` 가 PK 라 산책당 정확히 한 행이고, `services/walk.py`
    의 `activity.record_walk` 호출 네 곳 중 **분석 결과를 실제로 넘기는 두 곳**
    (`finalize_walk` 의 재시도 분기·최초 분기)이 그것을 세운다. 나머지 두 곳(첫 업로드·
    재접속 시 기존 산책 재확인)은 `analysis=None` 으로 불러 `record_walk` 가 head를
    만들지 않고 `activity_session_links` 연결만 하고 돌아간다 — 좌표만 있고 아직 봉인 전인
    산책은 애초에 head 가 생길 수 없다는 뜻이다. (`activity_game_enabled` 가 꺼져 있으면
    네 곳 다 아무것도 하지 않는다.)

    head 가 없는 산책은 **건수에는 들어가고 합계에는 안 들어갑니다.** 봉인이 안 끝난 것을
    0m 로 더하면 "걸었는데 0km" 가 되고, 건수에서까지 빼면 "안 걸었다" 가 됩니다. 둘 다
    거짓이라 두 수를 따로 냅니다.
    """
    walked_row = (await session.execute(_walked_stmt(pet_id, start, end))).one()
    measured_row = (await session.execute(_measured_stmt(pet_id, start, end))).one()
    return WalkActivitySums(
        walk_count=int(walked_row.walk_count or 0),
        measured_walk_count=int(measured_row.measured_walk_count or 0),
        distance_m=int(measured_row.distance_m or 0),
        moving_s=int(measured_row.moving_s or 0),
        last_started_at=walked_row.last_started_at,
    )


async def delete_walks_only_with(session: AsyncSession, pet_id: uuid.UUID) -> int:
    """**그 아이와만** 나간 산책을 지웁니다.

    강아지를 지울 때 부릅니다. 다른 아이와 같이 나간 산책은 **남깁니다** — 그 산책은
    남은 아이의 기록이기도 해서, 지우면 그 아이의 운동량이 통째로 빕니다. 그 산책에서
    이 아이만 빠지는 것은 조인 행의 `ON DELETE CASCADE` 가 알아서 합니다.

    아무도 안 붙은 산책은 애초에 조인 행이 없어 여기 걸리지 않습니다.

    :returns: 지운 산책 수.
    """
    others = WalkPet.__table__.alias("others")
    solo = (
        select(WalkPet.walk_id)
        .where(
            WalkPet.pet_id == pet_id,
            ~exists().where(
                others.c.walk_id == WalkPet.walk_id,
                others.c.pet_id != pet_id,
            ),
        )
    )
    result = await session.execute(delete(Walk).where(Walk.id.in_(solo)))
    return result.rowcount or 0


async def delete_all_for_owner(session: AsyncSession, app_user_id: uuid.UUID) -> int:
    """탈퇴한 회원의 산책을 전부 지웁니다.

    ``walk_point_chunks``(또는 아직 이관 전 DB의 ``walk_points``)와 ``walk_pets``는
    모두 ``walks.id ON DELETE CASCADE``라 이 DELETE 한 번에 같이 없어집니다.
    """
    result = await session.execute(
        delete(Walk).where(Walk.app_user_id == app_user_id)
    )
    return result.rowcount or 0


def add(session: AsyncSession, walk: Walk) -> Walk:
    session.add(walk)
    return walk


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
