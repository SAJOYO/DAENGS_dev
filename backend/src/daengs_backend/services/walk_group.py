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
import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import Walk
from daengs_backend.repositories import app_user as app_user_repo
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.repositories import pet_identity as identity_repo
from daengs_backend.repositories import pet_member as member_repo
from daengs_backend.repositories import walk_group as walk_group_repo
from daengs_backend.services import pet_identity as identity_service
from daengs_backend.services import pet_member as member_service
from daengs_backend.services.walk_session.chunk import decode_chunk

__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "PREVIEW_MAX_POINTS",
    "FeedCarer",
    "FeedScope",
    "FeedWalk",
    "GroupWalk",
    "GroupWalkFeed",
    "GroupWalkPage",
    "InvalidCursorError",
    "PetNotReadableError",
    "WalkFeedQuery",
    "WalkNotReadableError",
    "feed_page",
    "get_detail",
    "list_page",
    "readable_pet_ids",
    "readable_scope",
]

#: 카드 썸네일 한 장에 싣는 좌표 상한. 84dp 칸이라 이보다 많아도 그림이 안 달라집니다.
PREVIEW_MAX_POINTS = 48

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


# -- 통합 산책 목록 (`GET /app/pet-walks`) ---------------------------------------------------------
#
# 산책 기록 화면의 「산책별」이 내 산책과 공동 보호자 산책을 한 목록으로 보여 줍니다. 강아지 하나를
# 받는 #539 와 달리 **내가 볼 수 있는 강아지 전부**가 범위이고, 조건 전체의 합계와 보호자 후보를
# 같이 줍니다. 범위는 강아지(카드)마다 `readable_pet_ids` 를 그대로 불러 정합니다 — 권한 규칙을
# 여기서 다시 쓰지 않습니다.


@dataclass(frozen=True)
class FeedScope:
    """요청자 화면의 강아지 id(`/app/pets` 카드) → 그 카드로 읽을 수 있는 pet 행 id 묶음."""

    groups: dict[uuid.UUID, list[uuid.UUID]]

    def card_of(self) -> dict[uuid.UUID, uuid.UUID]:
        return {row: card for card, rows in self.groups.items() for row in rows}


async def readable_scope(session: AsyncSession, app_user_id: uuid.UUID) -> FeedScope:
    """내가 볼 수 있는 강아지 전부의 읽기 범위.

    카드는 `/app/pets` 와 같은 `collapse` 로 접습니다. 카드로 뽑힌 행이 내 접근 목록에 없는 드문
    경우(연결된 개인 행에만 돌보미)에는 **내가 접근하는 그 그룹의 행**으로 `readable_pet_ids` 를
    부릅니다 — 카드 id 로 부르면 404 이고, 그렇다고 그룹 전체로 넓히면 #539 의 보수적 규칙을
    어깁니다.
    """
    pets = await pet_repo.list_accessible(session, app_user_id)
    views = await identity_service.collapse(session, app_user_id, pets)
    accessible = {p.id: p for p in pets}
    groups: dict[uuid.UUID, list[uuid.UUID]] = {}
    for view in views:
        card = view.display
        reader = accessible.get(card.id)
        if reader is None:
            key = card.identity_id or card.id
            reader = next((p for p in pets if (p.identity_id or p.id) == key), None)
        if reader is None:
            continue
        try:
            groups[card.id] = await readable_pet_ids(session, app_user_id, reader.id)
        except PetNotReadableError:
            # 목록을 읽은 뒤 그 사이에 나갔거나 지워졌습니다 — 범위에서 뺄 뿐 실패시키지 않습니다.
            continue
    return FeedScope(groups)


@dataclass(frozen=True)
class WalkFeedQuery:
    """앱이 보낸 조건. 빈 튜플은 "그 조건 없음"입니다."""

    pet_ids: tuple[uuid.UUID, ...] = ()
    actor_ids: tuple[uuid.UUID, ...] = ()
    date_from: date | None = None
    date_through: date | None = None
    #: 날짜·월 경계를 자를 요청자 시간대(IANA). 부르는 쪽이 유효성을 먼저 봅니다.
    tz: str = "UTC"
    months: tuple[int, ...] = ()
    weather_codes: tuple[int, ...] = ()
    weather_missing: bool = False
    exclude_mine: bool = False


@dataclass(frozen=True)
class FeedWalk:
    group: GroupWalk
    #: 요청자 화면의 강아지 id 로 바꾼 태그.
    pet_ids: list[uuid.UUID]
    route_preview: list[list[tuple[float, float]]]


@dataclass(frozen=True)
class FeedCarer:
    app_user_id: uuid.UUID
    nickname: str | None
    is_me: bool
    pet_ids: list[uuid.UUID]


@dataclass(frozen=True)
class GroupWalkFeed:
    walks: list[FeedWalk]
    totals: walk_group_repo.WalkFeedTotals
    carers: list[FeedCarer]
    next_cursor: str | None


def route_preview(walk: Walk, max_points: int = PREVIEW_MAX_POINTS) -> list[list[tuple[float, float]]]:
    """카드 썸네일용으로 줄인 경로. `chain_index` 가 바뀌는 곳에서 끊고, 끝점은 늘 남깁니다."""
    points = sorted((p for chunk in walk.points for p in decode_chunk(chunk.payload)), key=lambda p: p.client_seq)
    if not points:
        return []
    step = max(1, math.ceil(len(points) / max_points))
    segments: list[list[tuple[float, float]]] = []
    chain: int | None = None
    for index, point in enumerate(points):
        if point.chain_index != chain:
            segments.append([])
            chain = point.chain_index
        last_of_chain = index + 1 == len(points) or points[index + 1].chain_index != point.chain_index
        if index % step == 0 or last_of_chain or not segments[-1]:
            segments[-1].append((round(float(point.lat), 6), round(float(point.lng), 6)))
    return segments


async def _carers(session: AsyncSession, app_user_id: uuid.UUID, scope: FeedScope) -> list[FeedCarer]:
    """보호자 조건 후보 — 카드마다 그 행들의 대표 ∪ 돌보미를 모아 **사람 단위로 중복 제거**합니다.
    나를 맨 앞에, 나머지는 닉네임 순."""
    rows = [row for ids in scope.groups.values() for row in ids]
    owners = await pet_repo.owners_by_ids(session, rows)
    related: dict[uuid.UUID, list[uuid.UUID]] = {}
    for card, ids in scope.groups.items():
        people = {owners[row] for row in ids if row in owners}
        for row in ids:
            people.update(await member_repo.list_members(session, row))
        for person in people:
            cards = related.setdefault(person, [])
            if card not in cards:
                cards.append(card)
    names = await app_user_repo.nicknames_by_ids(session, list(related))
    carers = [
        FeedCarer(app_user_id=uid, nickname=names.get(uid), is_me=uid == app_user_id, pet_ids=cards)
        for uid, cards in related.items()
    ]
    carers.sort(key=lambda c: (not c.is_me, c.nickname is None, c.nickname or "", str(c.app_user_id)))
    return carers


def _day_start(day: date, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=zone)


def _unique(values: Sequence) -> tuple:
    return tuple(dict.fromkeys(values))


async def feed_page(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    query: WalkFeedQuery,
    *,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> GroupWalkFeed:
    """조건에 맞는 산책 한 페이지 + 조건 전체 합계 + 보호자 후보.

    **앱이 보낸 강아지 id 는 범위 안의 카드일 때만** 씁니다. 볼 수 없는 id 만 보냈으면 범위를
    넓히지 않고 빈 결과입니다 — 조건을 무시하고 전체를 주면 "고른 강아지"가 조용히 바뀝니다.
    """
    scope = await readable_scope(session, app_user_id)
    before = decode_cursor(cursor) if cursor else None
    limit = max(1, min(limit, MAX_LIMIT))
    carers = await _carers(session, app_user_id, scope)
    cards = [pid for pid in _unique(query.pet_ids) if pid in scope.groups] if query.pet_ids else list(scope.groups)
    rows = _unique(row for card in cards for row in scope.groups[card])
    if not rows:
        return GroupWalkFeed([], walk_group_repo.WalkFeedTotals(0, 0, 0), carers, None)

    zone = ZoneInfo(query.tz)
    feed_filter = walk_group_repo.WalkFeedFilter(
        pet_ids=rows,
        actor_ids=_unique(query.actor_ids) or None,
        exclude_actor=app_user_id if query.exclude_mine else None,
        started_from=_day_start(query.date_from, zone) if query.date_from else None,
        started_before=_day_start(query.date_through + timedelta(days=1), zone) if query.date_through else None,
        months=tuple(sorted(set(query.months))) or None,
        tz=query.tz,
        weather_codes=tuple(sorted(set(query.weather_codes))),
        weather_missing=query.weather_missing,
    )
    fetched = await walk_group_repo.list_feed_page(session, feed_filter, limit=limit + 1, before=before)
    page = fetched[:limit]
    totals = await walk_group_repo.feed_totals(session, feed_filter)
    card_of = scope.card_of()
    decorated = await _decorate(session, app_user_id, list(rows), page)
    walks = [
        FeedWalk(
            group=shared,
            pet_ids=list(_unique(card_of[pid] for pid in shared.pet_ids if pid in card_of)),
            route_preview=route_preview(shared.walk),
        )
        for shared in decorated
    ]
    return GroupWalkFeed(
        walks=walks,
        totals=totals,
        carers=carers,
        next_cursor=encode_cursor(page[-1]) if len(fetched) > limit else None,
    )
