"""논리 강아지의 공동 조회 — 케어·약 중복 확인·산책·수행자 (MVP 결정 §7).

DB 는 쓰지 않습니다 (`test_care_events.py` 와 같은 규칙).

이 파일이 지키는 문장 하나:

> 같은 실제 강아지의 기록은 **여러 `pet_id` 에 갈려 쌓이지만 읽을 때 합쳐진다.**
> 쓰기는 언제나 **부른 사람의 행**에 남는다 — 그래야 연결을 풀면 제자리로 돌아간다.

**보류 범위는 안 열립니다** — 보행·스크리닝·대화·점령은 `member_condition` 을 그대로
두었으므로 여기 안 나옵니다.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from fakes import (
    FakeAdmin,
    FakeAppUser,
    FakeIdentity,
    FakePet,
    FakeSession,
    FakeWalk,
    FakeWalkPet,
    Store,
    install,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
from daengs_backend.repositories import care_event as care_repo
from daengs_backend.routers import care_event as care_router
from daengs_backend.services import care_event as care_service
from daengs_backend.services import pet_member as member_service

A = uuid.uuid4()  # 그룹 주보호자
B = uuid.uuid4()  # 연결한 공동 보호자
A_KAKAO = 5001
B_KAKAO = 5002

# ⚠️ **오늘 날짜로 만듭니다 — 고정 날짜를 박으면 안 됩니다.**
#
# 여기서 재는 것은 `GET /app/care-events/today` 이고, 서버는 「오늘」을 서울 자정
# 경계로 정합니다(`care_event.DAY_TIMEZONE`). 처음에는 `2026-09-12` 를 박아 뒀는데
# 그날이 지나자 아홉 건이 한꺼번에 깨졌습니다 — 합산이 전부 0 이 되어 마치 그룹
# 조회가 고장 난 것처럼 보였습니다.
#
# **미래로 잡아도 안 됩니다.** `CareEventCreate` 가 지금보다 10분 넘게 뒤인
# `occurred_at` 을 422 로 막습니다. 그래서 둘 다 **오늘 서울 안이면서 지금보다 과거**로
# 둡니다. 약 중복 확인 창을 재는 테스트는 두 기록에 **같은 시각**을 쓰므로 둘 사이
# 간격에 기대는 검사는 없습니다.
_SEOUL = ZoneInfo("Asia/Seoul")
_NOW = datetime.now(UTC)
_SEOUL_MIDNIGHT = datetime.combine(
    _NOW.astimezone(_SEOUL).date(), time(0, 1), tzinfo=_SEOUL
).astimezone(UTC)

SEOUL_EVENING = _NOW - timedelta(minutes=1)
SEOUL_MORNING = max(_SEOUL_MIDNIGHT, _NOW - timedelta(hours=2))

#: 위 두 시각이 속한 **서울 날짜**. `.date()` 는 UTC 날짜라 자정 근처에서 하루 어긋납니다.
SEOUL_DAY = SEOUL_MORNING.astimezone(_SEOUL).date()


@dataclass
class FakeCareEvent:
    pet_id: uuid.UUID
    kind: str
    occurred_at: datetime
    actor_app_user_id: uuid.UUID | None = None
    note: str | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    client_event_id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    s = Store(FakeAdmin(login_id="admin", password_hash="x", name="관리자", role="OWNER"))
    install(s, monkeypatch)
    s.add_app_user(FakeAppUser(kakao_id=A_KAKAO, id=A, nickname="아빠"))
    s.add_app_user(FakeAppUser(kakao_id=B_KAKAO, id=B, nickname="엄마"))

    # `fakes.install` 은 케어의 **읽기**만 대역을 겁니다(비서가 요청마다 읽기 때문).
    # 쓰기·삭제·약 중복 확인은 각 테스트 파일이 자기 것을 겁니다 — `test_care_events.py`
    # 와 같은 요령입니다.
    def add(session, event):
        # 진짜 DB 가 채우는 기본값 자리입니다 — 안 채우면 응답 직렬화가 터집니다.
        if getattr(event, "id", None) is None:
            event.id = uuid.uuid4()
        if getattr(event, "created_at", None) is None:
            event.created_at = datetime.now(UTC)
        s.care_events.append(event)
        return event

    async def get_by_client_event(session, pet_id, client_event_id):
        return next(
            (
                e
                for e in s.care_events
                if e.pet_id == pet_id and e.client_event_id == client_event_id
            ),
            None,
        )

    async def list_kind_between(session, pet_ids, kind, start, end):
        # **묶음**을 받습니다 — 약 중복 확인 창이 논리 그룹 전체를 봐야 교대 경계의
        # 중복 투약이 걸립니다 (MVP 결정 §7).
        wanted = set(pet_ids)
        return sorted(
            (
                e
                for e in s.care_events
                if e.pet_id in wanted and e.kind == kind and start <= e.occurred_at <= end
            ),
            key=lambda e: e.occurred_at,
            reverse=True,
        )

    async def get_deletable(session, app_user_id, event_id):
        # 진짜와 같게 **적은 사람 또는 그 행의 대표**만입니다 (docs/co-care.md §2).
        # 그룹으로 안 넓힙니다 — 이 변경이 건드리지 않은 경계입니다.
        owners = {pet.id: pet.app_user_id for pet in s.pets}
        return next(
            (
                e
                for e in s.care_events
                if e.id == event_id
                and (
                    e.actor_app_user_id == app_user_id
                    or owners.get(e.pet_id) == app_user_id
                )
            ),
            None,
        )

    async def delete(session, event):
        s.care_events = [e for e in s.care_events if e.id != event.id]

    monkeypatch.setattr(care_repo, "add", add)
    monkeypatch.setattr(care_repo, "get_by_client_event", get_by_client_event)
    monkeypatch.setattr(care_repo, "list_kind_between", list_kind_between)
    monkeypatch.setattr(care_repo, "get_deletable", get_deletable)
    monkeypatch.setattr(care_repo, "delete", delete)
    return s


def client_as(app_user_id: uuid.UUID) -> TestClient:
    app = FastAPI()
    app.include_router(care_router.router)
    app.dependency_overrides[
        next(iter(CurrentAppUser.__metadata__)).dependency
    ] = lambda: AppPrincipal(app_user_id=app_user_id)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def linked(store: Store) -> tuple[FakePet, FakePet]:
    """A 의 `롱이씨`(앵커)와 B 의 `롱롱씨`가 연결된 상태. B 는 A 의 행의 돌보미이기도 합니다."""
    a_pet = FakePet(app_user_id=A, name="롱이씨", breed="믹스")
    b_pet = FakePet(app_user_id=B, name="롱롱씨", breed="믹스")
    identity = FakeIdentity(owner_pet_id=a_pet.id)
    a_pet.identity_id = b_pet.identity_id = identity.id
    store.pets += [a_pet, b_pet]
    store.pet_identities.append(identity)
    store.pet_members.append((a_pet.id, B))
    return a_pet, b_pet


# ── 케어 공동 조회 ─────────────────────────────────────────────────────────


async def test_두_행에_갈린_케어가_합쳐_보인다(store: Store, linked):
    a_pet, b_pet = linked
    store.care_events += [
        FakeCareEvent(pet_id=a_pet.id, kind="meal", occurred_at=SEOUL_MORNING, actor_app_user_id=A),
        FakeCareEvent(pet_id=b_pet.id, kind="meal", occurred_at=SEOUL_EVENING, actor_app_user_id=B),
    ]

    body = client_as(B).get(f"/app/care-events/today?pet_id={b_pet.id}").json()
    assert body["meal"] == 2
    assert len(body["events"]) == 2


async def test_연결_이전_기록도_같이_보인다(store: Store, linked):
    """기록은 `pet_id` 에만 매여 있고 연결은 `pets` 쪽 칸이라, 시점 필터가 없습니다."""
    a_pet, b_pet = linked
    store.care_events.append(
        FakeCareEvent(
            pet_id=a_pet.id,
            kind="medication",
            occurred_at=SEOUL_MORNING,
            actor_app_user_id=A,
        )
    )
    body = client_as(B).get(f"/app/care-events/today?pet_id={b_pet.id}").json()
    assert body["medication"] == 1


async def test_A_와_B_가_같은_하루를_본다(store: Store, linked):
    a_pet, b_pet = linked
    store.care_events += [
        FakeCareEvent(pet_id=a_pet.id, kind="meal", occurred_at=SEOUL_MORNING, actor_app_user_id=A),
        FakeCareEvent(pet_id=b_pet.id, kind="snack", occurred_at=SEOUL_EVENING, actor_app_user_id=B),
    ]
    a_body = client_as(A).get(f"/app/care-events/today?pet_id={a_pet.id}").json()
    b_body = client_as(B).get(f"/app/care-events/today?pet_id={b_pet.id}").json()
    assert (a_body["meal"], a_body["snack"]) == (1, 1)
    assert (b_body["meal"], b_body["snack"]) == (1, 1)


async def test_연결_안_된_아이는_자기_것만_본다(store: Store):
    """그룹 조회가 남의 기록을 끌어오지 않는다는 회귀."""
    mine = FakePet(app_user_id=A, name="내아이", breed="믹스")
    theirs = FakePet(app_user_id=B, name="남의아이", breed="믹스")
    store.pets += [mine, theirs]
    store.care_events += [
        FakeCareEvent(pet_id=mine.id, kind="meal", occurred_at=SEOUL_MORNING),
        FakeCareEvent(pet_id=theirs.id, kind="meal", occurred_at=SEOUL_MORNING),
    ]
    body = client_as(A).get(f"/app/care-events/today?pet_id={mine.id}").json()
    assert body["meal"] == 1


async def test_쓰기는_부른_사람의_행에_남는다(store: Store, linked):
    """연결을 풀면 각자가 적은 것이 제자리에 남아야 합니다 (MVP 결정 §3)."""
    a_pet, b_pet = linked
    res = client_as(B).post(
        "/app/care-events",
        json={
            "pet_id": str(b_pet.id),
            "kind": "meal",
            "occurred_at": SEOUL_EVENING.isoformat(),
            "client_event_id": str(uuid.uuid4()),
        },
    )
    assert res.status_code == 201
    assert store.care_events[-1].pet_id == b_pet.id
    assert store.care_events[-1].actor_app_user_id == B


# ── 약 중복 확인 창 ────────────────────────────────────────────────────────


async def test_약_중복_확인_창이_그룹_전체를_본다(store: Store, linked):
    """A 가 아침에 준 약을 B 가 못 보면 **교대 경계의 중복 투약이 통째로 안 걸립니다** —
    그것이 이 확인의 존재 이유 전부입니다 (docs/co-care.md §4)."""
    a_pet, b_pet = linked
    store.care_events.append(
        FakeCareEvent(
            pet_id=a_pet.id,
            kind="medication",
            occurred_at=SEOUL_EVENING,
            actor_app_user_id=A,
        )
    )

    res = client_as(B).post(
        "/app/care-events",
        json={
            "pet_id": str(b_pet.id),
            "kind": "medication",
            "occurred_at": SEOUL_EVENING.isoformat(),
            "client_event_id": str(uuid.uuid4()),
        },
    )
    assert res.status_code == 409
    assert res.json()["detail"]["conflicts"]


async def test_confirm_이면_그룹_창을_지나간다(store: Store, linked):
    a_pet, b_pet = linked
    store.care_events.append(
        FakeCareEvent(
            pet_id=a_pet.id,
            kind="medication",
            occurred_at=SEOUL_EVENING,
            actor_app_user_id=A,
        )
    )
    res = client_as(B).post(
        "/app/care-events",
        json={
            "pet_id": str(b_pet.id),
            "kind": "medication",
            "occurred_at": SEOUL_EVENING.isoformat(),
            "client_event_id": str(uuid.uuid4()),
            "confirm": True,
        },
    )
    assert res.status_code == 201


# ── 산책 공동 조회 · 수행자 ────────────────────────────────────────────────


def _walk(store: Store, owner: uuid.UUID, pets: list[FakePet], at: datetime) -> FakeWalk:
    walk = FakeWalk(
        app_user_id=owner,
        client_session_id=uuid.uuid4(),
        started_at=at,
        ended_at=at,
    )
    walk.pets = [FakeWalkPet(pet_id=p.id) for p in pets]
    store.walks.append(walk)
    return walk


async def test_두_행의_산책이_합쳐_세어진다(store: Store, linked):
    a_pet, b_pet = linked
    _walk(store, A, [a_pet], SEOUL_MORNING)
    _walk(store, B, [b_pet], SEOUL_EVENING)

    body = client_as(B).get(f"/app/care-events/today?pet_id={b_pet.id}").json()
    assert body["walk"] == 2


async def test_같은_산책에_그룹_두_아이가_태그돼도_한_번만_센다(store: Store, linked):
    """`COUNT(DISTINCT Walk.id)` 와 `distinct()` 가 지키는 것 — 한 마리는 만들 수 없는 상황입니다."""
    a_pet, b_pet = linked
    _walk(store, A, [a_pet, b_pet], SEOUL_MORNING)

    body = client_as(B).get(f"/app/care-events/today?pet_id={b_pet.id}").json()
    assert body["walk"] == 1
    assert len(body["walk_rows"]) == 1


async def test_산책마다_누가_다녀왔는지_실린다(store: Store, linked):
    a_pet, b_pet = linked
    _walk(store, A, [a_pet], SEOUL_MORNING)
    _walk(store, B, [b_pet], SEOUL_EVENING)

    body = client_as(B).get(f"/app/care-events/today?pet_id={b_pet.id}").json()
    walkers = {row["actor"]["nickname"] for row in body["walk_rows"]}
    assert walkers == {"아빠", "엄마"}


async def test_구성원이_아닌_사람의_산책은_이름이_안_난다(store: Store, linked):
    """케어 `actor` 와 같은 규칙입니다 — 지금도 구성원일 때만 이름이 납니다."""
    a_pet, b_pet = linked
    stranger = uuid.uuid4()
    _walk(store, stranger, [a_pet], SEOUL_MORNING)

    body = client_as(B).get(f"/app/care-events/today?pet_id={b_pet.id}").json()
    row = body["walk_rows"][0]
    assert row["actor"]["app_user_id"] == str(stranger)
    assert row["actor"]["nickname"] is None


async def test_walk_수와_walk_rows_길이가_같다(store: Store, linked):
    """둘이 같은 질의에서 나오므로 어긋날 자리가 없습니다."""
    a_pet, b_pet = linked
    _walk(store, A, [a_pet], SEOUL_MORNING)
    _walk(store, B, [b_pet], SEOUL_EVENING)

    body = client_as(B).get(f"/app/care-events/today?pet_id={b_pet.id}").json()
    assert body["walk"] == len(body["walk_rows"])


async def test_연결_안_된_아이는_walk_rows_도_자기_것만(store: Store):
    mine = FakePet(app_user_id=A, name="내아이", breed="믹스")
    theirs = FakePet(app_user_id=B, name="남의아이", breed="믹스")
    store.pets += [mine, theirs]
    _walk(store, A, [mine], SEOUL_MORNING)
    _walk(store, B, [theirs], SEOUL_EVENING)

    body = client_as(A).get(f"/app/care-events/today?pet_id={mine.id}").json()
    assert body["walk"] == 1


# ── 서비스 층 직접 ─────────────────────────────────────────────────────────


async def test_day_summary_가_그룹_id_로_읽는다(store: Store, linked):
    a_pet, b_pet = linked
    store.care_events.append(
        FakeCareEvent(pet_id=a_pet.id, kind="meal", occurred_at=SEOUL_MORNING)
    )
    summary = await care_service.day_summary(None, B, b_pet.id, day=SEOUL_DAY)
    assert summary.counts.get("meal") == 1


async def test_삭제_권한은_안_넓어진다(store: Store, linked):
    """공동 보호자끼리는 서로의 기록을 못 지웁니다 — 이 변경이 건드리지 않은 경계입니다
    (docs/co-care.md §2, MVP 결정 §7)."""
    a_pet, _b = linked
    event = FakeCareEvent(
        pet_id=a_pet.id, kind="meal", occurred_at=SEOUL_MORNING, actor_app_user_id=A
    )
    store.care_events.append(event)

    res = client_as(B).delete(f"/app/care-events/{event.id}")
    assert res.status_code == 404
    assert event in store.care_events


# ── 나가기 뒤 — 끊기는 것은 "그때까지" 가 아니다 ───────────────────────────


async def test_자기_카드_id_로_나가면_그룹_케어가_끊긴다(store: Store, linked):
    """B 가 자기 카드 id 로 나간다(`display_pet_id`). 나간 **뒤에 생긴** 기록까지 안 보여야
    한다 — 멤버십·연결이 함께 풀리므로 공동 조회의 `pet_id` 묶음이 자기 행 하나로 줄어든다.

    반대로 **B 가 적은 자기 행의 기록은 그대로 남는다** — 나가기는 기록을 옮기거나 지우지
    않는다 (docs/co-care.md 「퇴장·내보내기」).
    """
    a_pet, b_pet = linked
    store.care_events += [
        FakeCareEvent(pet_id=a_pet.id, kind="meal", occurred_at=SEOUL_MORNING, actor_app_user_id=A),
        FakeCareEvent(pet_id=b_pet.id, kind="meal", occurred_at=SEOUL_MORNING, actor_app_user_id=B),
    ]

    await member_service.remove_member(FakeSession(store), B, b_pet.id, B)

    # 나간 뒤에 A 가 새로 남긴 것.
    store.care_events.append(
        FakeCareEvent(pet_id=a_pet.id, kind="snack", occurred_at=SEOUL_EVENING, actor_app_user_id=A)
    )

    after = client_as(B).get(f"/app/care-events/today?pet_id={b_pet.id}").json()
    assert (after["meal"], after["snack"]) == (1, 0), "나간 사람이 그룹 기록을 계속 본다"
    assert len(after["events"]) == 1
    # 앵커 행 id 로 직접 물어도 못 읽는다.
    assert client_as(B).get(f"/app/care-events/today?pet_id={a_pet.id}").status_code == 404
    # A 쪽에서도 나간 사람 행의 기록이 더는 안 섞인다.
    mine = client_as(A).get(f"/app/care-events/today?pet_id={a_pet.id}").json()
    assert (mine["meal"], mine["snack"]) == (1, 1)


async def test_내보내진_보호자도_그룹_케어를_못_본다(store: Store, linked):
    a_pet, b_pet = linked
    store.care_events.append(
        FakeCareEvent(pet_id=a_pet.id, kind="meal", occurred_at=SEOUL_MORNING, actor_app_user_id=A)
    )

    await member_service.remove_member(FakeSession(store), A, a_pet.id, B)

    assert client_as(B).get(f"/app/care-events/today?pet_id={b_pet.id}").json()["meal"] == 0
    assert client_as(B).get(f"/app/care-events/today?pet_id={a_pet.id}").status_code == 404
