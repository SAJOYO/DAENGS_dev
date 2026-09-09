"""routers/care_event.py + services/care_event.py — 케어 로그 (#332).

DB 는 쓰지 않습니다. 강아지는 `fakes.install` 이 바꿔치기한 `pet_repo` 로 오고, 케어 이벤트
리포지토리는 이 파일 안의 가짜가 대신합니다 — `fakes.py` 를 안 건드리는 것은 #331 과 같은
파일을 만지지 않으려는 것입니다. 여기서 보는 것은 **규칙**입니다: 같은 멱등키는 한 줄인가,
남의 강아지에 손댈 수 있는가, 기간 상한이 걸리는가, 배웅한 아이도 조회되는가, 하루 요약이
산책까지 세는가.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fakes import FakeAdmin, FakeAppUser, FakePet, Store, install
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
from daengs_backend.repositories import care_event as care_repo
from daengs_backend.repositories import walk as walk_repo
from daengs_backend.routers import care_event as care_router
from daengs_backend.services import care_event as care_service

OWNER = uuid.uuid4()
#: 돌보미. 기록하고 보지만, 프로필을 고치거나 남의 기록을 지우지는 못합니다 (docs/co-care.md).
CARER = uuid.uuid4()
STRANGER = uuid.uuid4()
SEOUL = ZoneInfo("Asia/Seoul")


@dataclass
class FakeCareEvent:
    actor_app_user_id: uuid.UUID
    pet_id: uuid.UUID
    kind: str
    occurred_at: datetime
    note: str | None
    client_event_id: uuid.UUID
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=lambda: datetime(2026, 9, 8, tzinfo=UTC))


class CareStore:
    """가짜 care_events. 진짜 리포지토리와 같은 정렬·필터 규칙을 씁니다."""

    def __init__(self) -> None:
        self.events: list[FakeCareEvent] = []
        #: pet_id → 산책 시작 시각들. walks 대역 — 하루 요약이 `walks` 를 세는 자리입니다.
        self.walk_starts: dict[uuid.UUID, list[datetime]] = {}


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    s = install(Store(FakeAdmin()), monkeypatch)
    s.add_app_user(FakeAppUser(kakao_id=1, id=OWNER))
    return s


@pytest.fixture
def care(store: Store, monkeypatch: pytest.MonkeyPatch) -> CareStore:
    cs = CareStore()

    def _pet_owner(pet_id):
        """그 아이의 대표. 삭제 판정이 "적은 사람 또는 대표" 라 대역도 pets 를 봐야 합니다."""
        return next((p.app_user_id for p in store.pets if p.id == pet_id), None)

    def add(session, event):
        # 진짜 모델은 CareEvent 지만 대역이 같은 칸을 갖고 있어 그대로 담습니다.
        fake = FakeCareEvent(
            actor_app_user_id=event.actor_app_user_id, pet_id=event.pet_id, kind=event.kind,
            occurred_at=event.occurred_at, note=event.note, client_event_id=event.client_event_id,
        )
        # 서비스가 commit 뒤 `event` 를 응답에 씁니다 — 진짜는 flush 가 id·created_at 을 채웁니다.
        event.id = fake.id
        event.created_at = fake.created_at
        cs.events.append(fake)
        return event

    async def get_deletable(session, app_user_id, event_id):
        # 진짜와 같게 **적은 사람 또는 그 아이의 대표** 입니다 (docs/co-care.md §2).
        return next(
            (
                e
                for e in cs.events
                if e.id == event_id
                and (
                    e.actor_app_user_id == app_user_id
                    or _pet_owner(e.pet_id) == app_user_id
                )
            ),
            None,
        )

    async def get_by_client_event(session, pet_id, client_event_id):
        return next(
            (e for e in cs.events if e.pet_id == pet_id and e.client_event_id == client_event_id),
            None,
        )

    def _between(_app_user_id, pet_id, start, end):
        # 진짜와 같게 **actor 로 안 거릅니다** — 기록의 주인은 강아지입니다 (docs/co-care.md §2).
        return [
            e for e in cs.events
            if e.pet_id == pet_id and start <= e.occurred_at < end
        ]

    async def list_between(session, app_user_id, pet_id, start, end):
        return sorted(
            _between(app_user_id, pet_id, start, end),
            key=lambda e: (-e.occurred_at.timestamp(), str(e.id)),
        )

    async def count_by_kind(session, app_user_id, pet_id, start, end):
        counts: dict[str, int] = {}
        for e in _between(app_user_id, pet_id, start, end):
            counts[e.kind] = counts.get(e.kind, 0) + 1
        return counts

    async def delete(session, event):
        cs.events = [e for e in cs.events if e.id != event.id]

    async def count_walks(session, app_user_id, pet_id, start, end):
        return sum(1 for t in cs.walk_starts.get(pet_id, []) if start <= t < end)

    monkeypatch.setattr(care_repo, "add", add)
    monkeypatch.setattr(care_repo, "get_deletable", get_deletable)
    monkeypatch.setattr(care_repo, "get_by_client_event", get_by_client_event)
    monkeypatch.setattr(care_repo, "list_between", list_between)
    monkeypatch.setattr(care_repo, "count_by_kind", count_by_kind)
    monkeypatch.setattr(care_repo, "delete", delete)
    monkeypatch.setattr(walk_repo, "count_for_pet_between", count_walks)
    return cs


def _client_for(app_user_id: uuid.UUID) -> TestClient:
    """그 사람으로 인증을 통과한 클라이언트. 토큰 검증은 test_app_auth 가 봅니다."""
    app = FastAPI()
    app.include_router(care_router.router)
    app.dependency_overrides[
        next(iter(CurrentAppUser.__metadata__)).dependency
    ] = lambda: AppPrincipal(app_user_id=app_user_id)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client(store: Store, care: CareStore) -> TestClient:
    """대표로 부릅니다 — 이 파일의 기본 화자입니다."""
    return _client_for(OWNER)


@pytest.fixture
def client_as(store: Store, care: CareStore):
    """다른 사람으로 같은 API 를 부릅니다.

    공동 돌봄의 규칙은 사람이 둘이라야 보입니다 — 돌보미가 적고 대표가 보는 것이
    이 기능의 전부라서, 한 사람짜리 클라이언트로는 아무것도 증명되지 않습니다.
    """
    return _client_for


@pytest.fixture
def pet(store: Store) -> FakePet:
    mine = FakePet(app_user_id=OWNER, name="보리", breed="maltese")
    store.pets.append(mine)
    return mine


def _body(pet_id: uuid.UUID, **kw: object) -> dict:
    body = {
        "pet_id": str(pet_id),
        "kind": "meal",
        "occurred_at": "2026-09-01T08:00:00+09:00",
        "client_event_id": str(uuid.uuid4()),
    }
    body.update(kw)
    return body


def _event(app_user_id: uuid.UUID, pet_id: uuid.UUID, kind: str, at: datetime) -> FakeCareEvent:
    return FakeCareEvent(
        actor_app_user_id=app_user_id, pet_id=pet_id, kind=kind, occurred_at=at,
        note=None, client_event_id=uuid.uuid4(),
    )


# ── 멱등 ───────────────────────────────────────────────────────────────


def test_같은_멱등키는_한_줄이고_두_번째는_200이다(client, pet, care) -> None:
    """탭 두 번·재시도가 두 줄이 되면 "밥 2번" 이 되고 아무 에러도 안 납니다."""
    body = _body(pet.id, note="사료 반만")
    first = client.post("/app/care-events", json=body)
    assert first.status_code == 201, first.text
    again = client.post("/app/care-events", json=body)
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert len(care.events) == 1
    assert care.events[0].note == "사료 반만"


def test_같은_키로_다른_내용이_와도_덮어쓰지_않는다(client, pet, care) -> None:
    body = _body(pet.id, kind="meal")
    client.post("/app/care-events", json=body)
    r = client.post("/app/care-events", json={**body, "kind": "snack"})
    assert r.status_code == 200
    assert r.json()["kind"] == "meal"
    assert [e.kind for e in care.events] == ["meal"]


def test_다른_아이에게는_같은_키가_다른_기록이다(client, store, care) -> None:
    a = FakePet(app_user_id=OWNER, name="a", breed="x")
    b = FakePet(app_user_id=OWNER, name="b", breed="x")
    store.pets.extend([a, b])
    key = str(uuid.uuid4())
    assert client.post("/app/care-events", json=_body(a.id, client_event_id=key)).status_code == 201
    assert client.post("/app/care-events", json=_body(b.id, client_event_id=key)).status_code == 201
    assert len(care.events) == 2


# ── 소유권 ─────────────────────────────────────────────────────────────


def test_남의_강아지는_404_다(client, store, care) -> None:
    """**404 다. 403 이 아니다.** 403 으로 나누면 그 id 가 존재한다는 것을 알려 주는 셈이다."""
    theirs = FakePet(app_user_id=STRANGER, name="남의개", breed="beagle")
    store.pets.append(theirs)
    q = {"pet_id": str(theirs.id)}
    assert client.post("/app/care-events", json=_body(theirs.id)).status_code == 404
    assert client.get("/app/care-events", params=q).status_code == 404
    assert client.get("/app/care-events/today", params=q).status_code == 404
    assert care.events == []


def test_남의_기록은_지울_수_없다(client, store, care) -> None:
    theirs = FakePet(app_user_id=STRANGER, name="남의개", breed="beagle")
    store.pets.append(theirs)
    care.events.append(_event(STRANGER, theirs.id, "meal", datetime(2026, 9, 1, 8, tzinfo=SEOUL)))
    r = client.delete(f"/app/care-events/{care.events[0].id}")
    assert r.status_code == 404
    assert len(care.events) == 1


def test_내_기록은_지워진다(client, pet, care) -> None:
    created = client.post("/app/care-events", json=_body(pet.id)).json()
    assert client.delete(f"/app/care-events/{created['id']}").status_code == 204
    assert care.events == []
    assert client.delete(f"/app/care-events/{created['id']}").status_code == 404


# ── 기간 ───────────────────────────────────────────────────────────────


def test_기간_상한을_넘으면_422(client, pet) -> None:
    too_wide = {
        "pet_id": str(pet.id),
        "from": "2026-07-01T00:00:00+09:00",
        "to": "2026-09-01T00:00:00+09:00",
    }
    r = client.get("/app/care-events", params=too_wide)
    assert r.status_code == 422
    assert str(care_service.MAX_RANGE.days) in r.json()["detail"]


def test_뒤집힌_기간도_422(client, pet) -> None:
    reversed_window = {
        "pet_id": str(pet.id),
        "from": "2026-09-02T00:00:00+09:00",
        "to": "2026-09-01T00:00:00+09:00",
    }
    assert client.get("/app/care-events", params=reversed_window).status_code == 422


def test_timezone_없는_기간은_422(client, pet) -> None:
    naive = {"pet_id": str(pet.id), "from": "2026-09-01T00:00:00"}
    r = client.get("/app/care-events", params=naive)
    assert r.status_code == 422
    assert "timezone" in r.json()["detail"]


def test_기간을_안_보내면_최근_7일이고_창을_같이_돌려준다(client, pet, care) -> None:
    r = client.get("/app/care-events", params={"pet_id": str(pet.id)})
    assert r.status_code == 200, r.text
    body = r.json()
    start = datetime.fromisoformat(body["start"])
    end = datetime.fromisoformat(body["end"])
    assert end - start == care_service.DEFAULT_RANGE
    assert body["events"] == []


def test_목록은_최근_먼저이고_창_밖은_빠진다(client, pet, care) -> None:
    for hour in (7, 12, 19):
        at = f"2026-09-01T{hour:02d}:00:00+09:00"
        client.post("/app/care-events", json=_body(pet.id, occurred_at=at))
    client.post("/app/care-events", json=_body(pet.id, occurred_at="2026-08-01T08:00:00+09:00"))
    window = {
        "pet_id": str(pet.id),
        "from": "2026-09-01T00:00:00+09:00",
        "to": "2026-09-02T00:00:00+09:00",
    }
    events = client.get("/app/care-events", params=window).json()["events"]
    hours = [datetime.fromisoformat(e["occurred_at"]).astimezone(SEOUL).hour for e in events]
    assert hours == [19, 12, 7]


# ── 배웅한 아이 ───────────────────────────────────────────────────────


def test_배웅한_아이도_조회된다(client, store, care) -> None:
    """배웅은 행을 안 지웁니다 — 있었던 일을 적어 두는 것이라 기록도 그대로 보입니다."""
    gone = FakePet(app_user_id=OWNER, name="첫째", breed="x", farewell_on=date(2026, 9, 1))
    store.pets.append(gone)
    care.events.append(_event(OWNER, gone.id, "medication", datetime(2026, 8, 30, 9, tzinfo=SEOUL)))
    window = {
        "pet_id": str(gone.id),
        "from": "2026-08-25T00:00:00+09:00",
        "to": "2026-09-02T00:00:00+09:00",
    }
    r = client.get("/app/care-events", params=window)
    assert r.status_code == 200
    assert [e["kind"] for e in r.json()["events"]] == ["medication"]
    day = {"pet_id": str(gone.id), "day": "2026-08-30"}
    assert client.get("/app/care-events/today", params=day).json()["medication"] == 1


# ── 하루 요약 ─────────────────────────────────────────────────────────


def test_하루_요약은_밥_약_간식에_산책까지_센다(client, pet, care) -> None:
    """밥 2 · 약 1 · 간식 3 · 산책 1 을 한 번에. 산책은 walks 에서 세지 여기 적지 않습니다."""
    day = "2026-09-01"
    plan = (("meal", 8), ("meal", 19), ("medication", 8), ("snack", 10), ("snack", 15), ("snack", 21))
    for kind, hour in plan:
        at = f"{day}T{hour:02d}:00:00+09:00"
        client.post("/app/care-events", json=_body(pet.id, kind=kind, occurred_at=at))
    # 전날 밤 것은 서울 자정 앞이라 안 들어갑니다.
    client.post("/app/care-events", json=_body(pet.id, kind="meal", occurred_at="2026-08-31T23:30:00+09:00"))
    care.walk_starts[pet.id] = [
        datetime(2026, 9, 1, 7, 30, tzinfo=SEOUL),
        datetime(2026, 8, 31, 18, 0, tzinfo=SEOUL),
    ]
    r = client.get("/app/care-events/today", params={"pet_id": str(pet.id), "day": day})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["meal"], body["medication"], body["snack"], body["walk"]) == (2, 1, 3, 1)
    assert body["day"] == day and body["timezone"] == "Asia/Seoul"
    assert datetime.fromisoformat(body["start"]) == datetime(2026, 9, 1, tzinfo=SEOUL)
    assert len(body["events"]) == 6


def test_하루_경계는_UTC_가_아니라_서울_자정이다() -> None:
    """서버 시간으로 자르면 밤 9시 뒤의 저녁밥이 "내일" 로 갑니다."""
    start, end = care_service.day_bounds(date(2026, 9, 8))
    assert start.astimezone(UTC) == datetime(2026, 9, 7, 15, tzinfo=UTC)
    assert end - start == timedelta(days=1)


# ── 본문 검증 ─────────────────────────────────────────────────────────


def test_timezone_없는_시각은_422(client, pet) -> None:
    r = client.post("/app/care-events", json=_body(pet.id, occurred_at="2026-09-01T08:00:00"))
    assert r.status_code == 422


def test_먼_미래_시각은_422(client, pet) -> None:
    """내일 저녁 약 은 기록이 아니라 계획입니다 — 알림(F5)의 일입니다."""
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    assert client.post("/app/care-events", json=_body(pet.id, occurred_at=tomorrow)).status_code == 422


def test_walk_는_kind_가_아니다(client, pet) -> None:
    """산책은 walks 가 진실입니다. 한 사실이 두 곳에 있으면 반드시 어긋납니다."""
    assert client.post("/app/care-events", json=_body(pet.id, kind="walk")).status_code == 422


def test_공백뿐인_메모는_None_이다(client, pet, care) -> None:
    r = client.post("/app/care-events", json=_body(pet.id, note="   "))
    assert r.status_code == 201
    assert r.json()["note"] is None
    assert care.events[0].note is None


# ── 공동 돌봄 (docs/co-care.md §2) ────────────────────────────────────


def test_돌보미가_기록하고_대표가_본다(client, client_as, store, pet, care) -> None:
    """이 기능의 전부입니다 — 아빠가 아침에 적은 밥이 내 오늘 요약에 보입니다.

    요약이 사람이 아니라 **강아지** 기준이라야 보입니다. actor 로 거르면 각자 자기가
    적은 것만 보게 되어, 두 사람이 같은 밥을 두 번 줍니다.
    """
    store.pet_members.append((pet.id, CARER))
    body = _body(pet.id, occurred_at="2026-09-09T08:12:00+09:00")
    assert client_as(CARER).post("/app/care-events", json=body).status_code == 201

    seen = client.get(
        "/app/care-events/today", params={"pet_id": str(pet.id), "day": "2026-09-09"}
    ).json()
    assert seen["meal"] == 1
    assert len(seen["events"]) == 1


def test_돌보미가_아니면_기록도_조회도_404(client_as, pet, care) -> None:
    """구성원 판정이 열린 것은 돌보미까지입니다 — 초대받지 않은 사람은 그대로 404 입니다."""
    outsider = client_as(STRANGER)
    assert outsider.post("/app/care-events", json=_body(pet.id)).status_code == 404
    assert outsider.get("/app/care-events", params={"pet_id": str(pet.id)}).status_code == 404
    assert care.events == []


# ── 삭제 권한: 기록한 사람 또는 대표 ─────────────────────────────────


def test_기록한_사람이_자기_기록을_지운다(client_as, store, pet, care) -> None:
    store.pet_members.append((pet.id, CARER))
    carer = client_as(CARER)
    created = carer.post("/app/care-events", json=_body(pet.id)).json()
    assert carer.delete(f"/app/care-events/{created['id']}").status_code == 204
    assert care.events == []


def test_대표는_돌보미의_오기록을_지운다(client, client_as, store, pet, care) -> None:
    """돌봄 기록은 **강아지 것**입니다 (docs/co-care.md 결정 ①).

    actor 로만 거르면 대표가 자기 아이의 잘못 적힌 줄을 영영 못 지웁니다 — 적은 사람이
    나가면 그 줄은 아무도 못 건드립니다.
    """
    store.pet_members.append((pet.id, CARER))
    created = client_as(CARER).post("/app/care-events", json=_body(pet.id)).json()
    assert client.delete(f"/app/care-events/{created['id']}").status_code == 204
    assert care.events == []


def test_다른_돌보미는_남의_기록을_못_지운다(client_as, store, pet, care) -> None:
    """열어 준 것은 **적은 사람과 대표**까지입니다. 돌보미끼리는 서로의 기록을 못 지웁니다."""
    other = uuid.uuid4()
    store.pet_members.extend([(pet.id, CARER), (pet.id, other)])
    created = client_as(CARER).post("/app/care-events", json=_body(pet.id)).json()
    assert client_as(other).delete(f"/app/care-events/{created['id']}").status_code == 404
    assert len(care.events) == 1


def test_남남은_우리_아이의_기록을_못_지운다(client, client_as, pet, care) -> None:
    created = client.post("/app/care-events", json=_body(pet.id)).json()
    assert client_as(STRANGER).delete(f"/app/care-events/{created['id']}").status_code == 404
    assert len(care.events) == 1
