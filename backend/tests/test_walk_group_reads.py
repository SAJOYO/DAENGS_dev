"""산책 기록 공동 조회 — `GET /app/pets/{pet_id}/walks[/{walk_id}]` (docs/co-care.md 「산책 기록 공동 조회」).

DB 는 쓰지 않습니다 (`test_pet_identity.py` 와 같은 규칙). 공동 조회 리포지토리
(`repositories/walk_group.py`)는 이 파일 안에서 가짜 저장소로 갈아 끼웁니다 — SQL 자체는
실DB 검증 몫입니다.

그림:

    A 의 `롱이씨`(a_pet) ── 연결 ── B 의 `롱롱씨`(b_pet)     A = 그룹 주보호자, B = 연결 참여
    O 의 `맥스`(s_pet) ── J 는 연결 없이 참여한 돌보미
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fakes import (
    FakeAdmin,
    FakeAppUser,
    FakeIdentity,
    FakePet,
    FakeWalk,
    FakeWalkPet,
    FakeWalkPointChunk,
    Store,
    install,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
from daengs_backend.repositories import walk_group as walk_group_repo
from daengs_backend.repositories.walk_group import WalkSummaryNumbers
from daengs_backend.routers import pet_member as pet_member_router
from daengs_backend.routers import pet_walks as pet_walks_router
from daengs_backend.routers import walk as walk_router
from daengs_backend.schemas.walk import WalkPointUpload
from daengs_backend.services.walk_session.chunk import encode_chunk

A = uuid.uuid4()
B = uuid.uuid4()
C = uuid.uuid4()  # 연결 전 B 의 강아지를 돌보던 사람
O = uuid.uuid4()
J = uuid.uuid4()
X = uuid.uuid4()  # 무관한 사람

T0 = datetime(2026, 9, 10, 8, 0, tzinfo=UTC)

ITEM_KEYS = {
    "id", "started_at", "ended_at", "duration_s", "distance_m", "moving_s", "actor", "is_mine", "pet_ids",
}


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    s = Store(FakeAdmin(login_id="admin", password_hash="x", name="관리자", role="OWNER"))
    install(s, monkeypatch)
    for kakao, uid, nick in [(1, A, "에이"), (2, B, "비"), (3, C, "씨"), (4, O, "오"), (5, J, "제이"), (6, X, "엑스")]:
        s.add_app_user(FakeAppUser(kakao_id=kakao, id=uid, nickname=nick))

    async def list_group_page(session, pet_ids, *, limit, before=None):
        wanted = set(pet_ids)
        rows = sorted(
            (w for w in s.walks if wanted & set(w.pet_ids)),
            key=lambda w: (w.started_at, w.id),
            reverse=True,
        )
        if before is not None:
            rows = [w for w in rows if (w.started_at, w.id) < before]
        return rows[:limit]

    async def get_in_group(session, pet_ids, walk_id):
        wanted = set(pet_ids)
        return next((w for w in s.walks if w.id == walk_id and wanted & set(w.pet_ids)), None)

    async def latest_numbers(session, walk_ids):
        wanted = set(walk_ids)
        out = {}
        # 최근 세대가 이깁니다 — 오래된 것부터 덮어써서 마지막 것이 남게 합니다.
        for a in sorted(s.walk_analyses, key=lambda a: (a.derived_at, a.id)):
            if a.walk_id in wanted:
                out[a.walk_id] = WalkSummaryNumbers(a.moving_distance_m, a.moving_s)
        return out

    monkeypatch.setattr(walk_group_repo, "list_group_page", list_group_page)
    monkeypatch.setattr(walk_group_repo, "get_in_group", get_in_group)
    monkeypatch.setattr(walk_group_repo, "latest_numbers", latest_numbers)
    return s


@pytest.fixture
def linked(store: Store) -> tuple[FakePet, FakePet]:
    a_pet = FakePet(app_user_id=A, name="롱이씨", breed="dog_pug")
    b_pet = FakePet(app_user_id=B, name="롱롱씨", breed="dog_beagle")
    identity = FakeIdentity(owner_pet_id=a_pet.id)
    a_pet.identity_id = identity.id
    b_pet.identity_id = identity.id
    store.pets += [a_pet, b_pet]
    store.pet_identities.append(identity)
    # 연결 수락은 멤버십과 연결을 같이 만듭니다 — B 는 A 행의 돌보미이기도 합니다.
    store.pet_members.append((a_pet.id, B))
    return a_pet, b_pet


@pytest.fixture
def solo(store: Store) -> FakePet:
    s_pet = FakePet(app_user_id=O, name="맥스", breed="믹스")
    store.pets.append(s_pet)
    store.pet_members.append((s_pet.id, J))
    return s_pet


def add_walk(store: Store, owner: uuid.UUID, pets: list[FakePet], started: datetime, *, points: int = 2) -> FakeWalk:
    uploads = [
        WalkPointUpload(
            client_seq=i,
            chain_index=0,
            at=started + timedelta(seconds=i),
            lat=Decimal("37.497800") + Decimal(i) / Decimal(10000),
            lng=Decimal("127.027500"),
            accuracy_m=5.0,
        )
        for i in range(points)
    ]
    walk = FakeWalk(
        app_user_id=owner,
        client_session_id=uuid.uuid4(),
        started_at=started,
        ended_at=started + timedelta(minutes=30),
        pets=[FakeWalkPet(pet_id=p.id) for p in pets],
        points=[FakeWalkPointChunk(seq_from=0, seq_to=points - 1, point_count=points, payload=encode_chunk(uploads))],
    )
    store.walks.append(walk)
    return walk


def add_analysis(store: Store, walk: FakeWalk, distance: int, moving: int, at: datetime) -> None:
    store.walk_analyses.append(
        SimpleNamespace(id=uuid.uuid4(), walk_id=walk.id, moving_distance_m=distance, moving_s=moving, derived_at=at)
    )


def client_as(uid: uuid.UUID) -> TestClient:
    app = FastAPI()
    app.include_router(pet_walks_router.router)
    app.include_router(walk_router.router)
    app.include_router(pet_member_router.router)
    app.dependency_overrides[next(iter(CurrentAppUser.__metadata__)).dependency] = lambda: AppPrincipal(app_user_id=uid)
    app.dependency_overrides[walk_router.get_walk_weather_lookup] = lambda: None
    return TestClient(app, raise_server_exceptions=False)


def walk_ids(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [w["id"] for w in response.json()["walks"]]


# ── 연결 없이 참여 / 연결 참여 ─────────────────────────────────────────────


def test_연결_없이_참여한_보호자는_대표의_산책을_본다(store: Store, solo: FakePet):
    walk = add_walk(store, O, [solo], T0)

    body = client_as(J).get(f"/app/pets/{solo.id}/walks").json()
    assert [w["id"] for w in body["walks"]] == [str(walk.id)]
    item = body["walks"][0]
    assert item["actor"] == {"app_user_id": str(O), "nickname": "오"}
    assert item["is_mine"] is False
    assert client_as(O).get(f"/app/pets/{solo.id}/walks").json()["walks"][0]["is_mine"] is True


def test_연결한_보호자와_주보호자가_서로의_산책을_본다(store: Store, linked):
    a_pet, b_pet = linked
    wa = add_walk(store, A, [a_pet], T0)
    wb = add_walk(store, B, [b_pet], T0 + timedelta(hours=1))

    assert walk_ids(client_as(B).get(f"/app/pets/{b_pet.id}/walks")) == [str(wb.id), str(wa.id)]
    assert walk_ids(client_as(A).get(f"/app/pets/{a_pet.id}/walks")) == [str(wb.id), str(wa.id)]

    walks = client_as(B).get(f"/app/pets/{b_pet.id}/walks").json()["walks"]
    by_id = {w["id"]: w for w in walks}
    assert by_id[str(wa.id)]["actor"]["nickname"] == "에이"
    assert by_id[str(wb.id)]["actor"]["nickname"] == "비"
    assert by_id[str(wb.id)]["is_mine"] is True and by_id[str(wa.id)]["is_mine"] is False


# ── 수행자와 기본 정보 · 목록·상세·경로 ────────────────────────────────────


def test_일시_시간_거리와_수행자가_실린다(store: Store, linked):
    a_pet, b_pet = linked
    measured = add_walk(store, A, [a_pet], T0)
    add_analysis(store, measured, 900, 1500, T0 + timedelta(hours=1))
    add_analysis(store, measured, 1234, 1600, T0 + timedelta(hours=2))  # 최신 세대
    unmeasured = add_walk(store, A, [a_pet], T0 - timedelta(days=1))

    walks = client_as(B).get(f"/app/pets/{b_pet.id}/walks").json()["walks"]
    by_id = {w["id"]: w for w in walks}
    m = by_id[str(measured.id)]
    assert m["started_at"].startswith("2026-09-10T08:00:00")
    assert m["duration_s"] == 1800
    assert (m["distance_m"], m["moving_s"]) == (1234, 1600)
    u = by_id[str(unmeasured.id)]
    assert (u["distance_m"], u["moving_s"]) == (None, None)
    assert u["duration_s"] == 1800


def test_나간_보호자는_이름이_비어도_산책은_남는다(store: Store, solo: FakePet):
    walk = add_walk(store, J, [solo], T0)
    store.pet_members.remove((solo.id, J))  # 탈퇴·내보내기로 멤버십만 사라진 상태

    item = client_as(O).get(f"/app/pets/{solo.id}/walks").json()["walks"][0]
    assert item["id"] == str(walk.id)
    assert item["actor"] == {"app_user_id": str(J), "nickname": None}


def test_목록에는_좌표가_없고_상세에는_경로가_있다(store: Store, linked):
    a_pet, b_pet = linked
    walk = add_walk(store, A, [a_pet], T0, points=3)

    item = client_as(B).get(f"/app/pets/{b_pet.id}/walks").json()["walks"][0]
    assert "points" not in item

    detail = client_as(B).get(f"/app/pets/{b_pet.id}/walks/{walk.id}")
    assert detail.status_code == 200, detail.text
    points = detail.json()["points"]
    assert len(points) == 3
    assert [set(p) for p in points] == [{"at", "lat", "lng"}] * 3
    assert [p["at"] for p in points] == sorted(p["at"] for p in points)


def test_공동_조회_응답에_게임_필드가_없다(store: Store, linked):
    """게임·점령 공유는 이번 결정으로 후순위 보류다 — 키 집합을 고정한다."""
    a_pet, b_pet = linked
    walk = add_walk(store, A, [a_pet], T0)

    body = client_as(B).get(f"/app/pets/{b_pet.id}/walks").json()
    assert set(body) == {"pet_id", "walks", "next_cursor"}
    assert set(body["walks"][0]) == ITEM_KEYS
    detail = client_as(B).get(f"/app/pets/{b_pet.id}/walks/{walk.id}").json()
    assert set(detail) == ITEM_KEYS | {"points"}
    assert set(detail["actor"]) == {"app_user_id", "nickname"}


# ── 중복 제거 · 페이지네이션 ───────────────────────────────────────────────


def test_그룹의_두_행에_태그된_산책은_한_번만_나온다(store: Store, linked):
    a_pet, b_pet = linked
    both = add_walk(store, A, [a_pet, b_pet], T0)

    walks = client_as(B).get(f"/app/pets/{b_pet.id}/walks").json()["walks"]
    assert [w["id"] for w in walks] == [str(both.id)]
    assert set(walks[0]["pet_ids"]) == {str(a_pet.id), str(b_pet.id)}


def test_커서로_끊어_읽으면_빠짐도_겹침도_없다(store: Store, linked):
    a_pet, b_pet = linked
    # 같은 시각 둘을 섞어 id 로 갈라지는 경계도 본다.
    made = [add_walk(store, A, [a_pet], T0 - timedelta(hours=i // 2)) for i in range(5)]
    expected = [str(w.id) for w in sorted(made, key=lambda w: (w.started_at, w.id), reverse=True)]

    seen, cursor, pages = [], None, 0
    while True:
        params = {"limit": 2} | ({"cursor": cursor} if cursor else {})
        body = client_as(B).get(f"/app/pets/{b_pet.id}/walks", params=params).json()
        seen += [w["id"] for w in body["walks"]]
        cursor = body["next_cursor"]
        pages += 1
        if cursor is None:
            break
    assert seen == expected
    assert pages == 3


def test_읽을_수_없는_커서는_422_지만_남의_강아지는_먼저_404(store: Store, linked, solo):
    _a, b_pet = linked
    assert client_as(B).get(f"/app/pets/{b_pet.id}/walks", params={"cursor": "!!bad"}).status_code == 422
    assert client_as(B).get(f"/app/pets/{solo.id}/walks", params={"cursor": "!!bad"}).status_code == 404


def test_limit_은_1_에서_50_사이다(store: Store, linked):
    _a, b_pet = linked
    assert client_as(B).get(f"/app/pets/{b_pet.id}/walks", params={"limit": 51}).status_code == 422
    assert client_as(B).get(f"/app/pets/{b_pet.id}/walks", params={"limit": 0}).status_code == 422


# ── 접근 차단 ──────────────────────────────────────────────────────────────


def test_무관한_사용자와_강아지_id_는_404(store: Store, linked, solo):
    a_pet, _b = linked
    walk = add_walk(store, A, [a_pet], T0)

    assert client_as(X).get(f"/app/pets/{a_pet.id}/walks").status_code == 404
    assert client_as(X).get(f"/app/pets/{a_pet.id}/walks/{walk.id}").status_code == 404
    assert client_as(B).get(f"/app/pets/{solo.id}/walks").status_code == 404
    assert client_as(B).get(f"/app/pets/{uuid.uuid4()}/walks").status_code == 404


def test_그룹_밖_산책_id_는_404(store: Store, linked, solo):
    """산책 id 만 알아서는 못 읽는다 — 볼 수 있는 강아지에 태그된 산책이어야 한다."""
    _a, b_pet = linked
    other = add_walk(store, O, [solo], T0)

    assert client_as(B).get(f"/app/pets/{b_pet.id}/walks/{other.id}").status_code == 404
    assert client_as(B).get(f"/app/pets/{b_pet.id}/walks/{uuid.uuid4()}").status_code == 404


def test_함께_태그된_그룹_밖_강아지는_드러나지_않는다(store: Store, linked):
    a_pet, b_pet = linked
    a_other = FakePet(app_user_id=A, name="다른아이", breed="믹스")
    store.pets.append(a_other)
    walk = add_walk(store, A, [a_pet, a_other], T0)

    item = client_as(B).get(f"/app/pets/{b_pet.id}/walks").json()["walks"][0]
    assert item["pet_ids"] == [str(a_pet.id)]
    detail = client_as(B).get(f"/app/pets/{b_pet.id}/walks/{walk.id}").json()
    assert detail["pet_ids"] == [str(a_pet.id)]


def test_연결된_개인_행에만_돌보미인_사람은_자기_행_산책만_본다(store: Store, linked):
    """연결 전 B 의 강아지를 돌보던 C 의 그룹 접근 범위는 아직 결정 전이다(#538 검토).
    이 기능은 그 결론을 앞서 넓히지 않는다."""
    a_pet, b_pet = linked
    store.pet_members.append((b_pet.id, C))
    wa = add_walk(store, A, [a_pet], T0)
    wb = add_walk(store, B, [b_pet], T0 + timedelta(hours=1))

    assert walk_ids(client_as(C).get(f"/app/pets/{b_pet.id}/walks")) == [str(wb.id)]
    assert client_as(C).get(f"/app/pets/{b_pet.id}/walks/{wa.id}").status_code == 404
    assert client_as(C).get(f"/app/pets/{a_pet.id}/walks").status_code == 404


# ── 나가기 · 내보내기 · 탈퇴 ────────────────────────────────────────────────


def test_내보내진_보호자는_그룹_산책을_목록_상세_경로_모두_못_본다(store: Store, linked):
    a_pet, b_pet = linked
    wa = add_walk(store, A, [a_pet], T0)
    wb = add_walk(store, B, [b_pet], T0 + timedelta(hours=1))

    assert client_as(A).delete(f"/app/pets/{a_pet.id}/members/{B}").status_code == 204

    assert walk_ids(client_as(B).get(f"/app/pets/{b_pet.id}/walks")) == [str(wb.id)]
    assert client_as(B).get(f"/app/pets/{b_pet.id}/walks/{wa.id}").status_code == 404
    assert client_as(B).get(f"/app/pets/{a_pet.id}/walks").status_code == 404
    assert client_as(B).get(f"/app/pets/{a_pet.id}/walks/{wa.id}").status_code == 404
    # 주보호자 쪽에서도 나간 사람 행의 산책이 더는 그룹으로 안 섞인다.
    assert walk_ids(client_as(A).get(f"/app/pets/{a_pet.id}/walks")) == [str(wa.id)]


def test_스스로_나간_보호자도_그룹_산책을_못_본다(store: Store, linked):
    a_pet, b_pet = linked
    wa = add_walk(store, A, [a_pet], T0)

    assert client_as(B).delete(f"/app/pets/{a_pet.id}/members/{B}").status_code == 204

    assert walk_ids(client_as(B).get(f"/app/pets/{b_pet.id}/walks")) == []
    assert client_as(B).get(f"/app/pets/{b_pet.id}/walks/{wa.id}").status_code == 404


def test_연결_없이_참여했다_나간_보호자는_404(store: Store, solo: FakePet):
    walk = add_walk(store, O, [solo], T0)
    assert client_as(J).delete(f"/app/pets/{solo.id}/members/{J}").status_code == 204

    assert client_as(J).get(f"/app/pets/{solo.id}/walks").status_code == 404
    assert client_as(J).get(f"/app/pets/{solo.id}/walks/{walk.id}").status_code == 404


def test_탈퇴한_보호자는_그룹_산책을_못_본다(store: Store, linked):
    """탈퇴는 트리거가 멤버십을 지우고 자기 강아지를 지운다 — 가짜 저장소에서 그 결과를 흉내 낸다."""
    a_pet, b_pet = linked
    wa = add_walk(store, A, [a_pet], T0)
    store.pet_members = [row for row in store.pet_members if row[1] != B]
    store.pets.remove(b_pet)

    assert client_as(B).get(f"/app/pets/{a_pet.id}/walks").status_code == 404
    assert client_as(B).get(f"/app/pets/{a_pet.id}/walks/{wa.id}").status_code == 404
    assert client_as(B).get(f"/app/pets/{b_pet.id}/walks").status_code == 404


# ── 쓰기는 그대로 작성자만 · 개인 산책 회귀 ─────────────────────────────────


def test_공동_보호자는_남의_산책에_좌표_봉인_기록보정을_못_한다(store: Store, linked):
    a_pet, _b = linked
    wa = add_walk(store, A, [a_pet], T0)
    point = {"client_seq": 5, "chain_index": 0, "at": T0.isoformat(), "lat": "37.5", "lng": "127.0"}

    b = client_as(B)
    assert b.post(f"/app/walks/{wa.id}/points", json={"points": [point]}).status_code == 404
    finalize = {"expected_point_count": 2, "terminal_client_seq": 1}
    assert b.post(f"/app/walks/{wa.id}/finalize", json=finalize).status_code == 404
    repair = {
        "contract_version": "gps-recording-v1",
        "policy_version": "gps-recording-eligibility-v1",
        "raw_input_fingerprint": "sha256:" + "0" * 64,
        "points": [point | {"recording_eligible": True}],
    }
    assert b.put(f"/app/walks/{wa.id}/recording-evidence", json=repair).status_code == 404
    assert b.get(f"/app/walks/{wa.id}").status_code == 404
    assert len(wa.points) == 1 and wa.points[0].point_count == 2


def test_개인_산책_목록은_여전히_내_것만이다(store: Store, linked):
    a_pet, b_pet = linked
    wa = add_walk(store, A, [a_pet], T0)
    wb = add_walk(store, B, [b_pet], T0 + timedelta(hours=1))

    assert [w["id"] for w in client_as(A).get("/app/walks").json()["walks"]] == [str(wa.id)]
    assert [w["id"] for w in client_as(B).get("/app/walks").json()["walks"]] == [str(wb.id)]
    assert client_as(A).get(f"/app/walks/{wa.id}").status_code == 200
