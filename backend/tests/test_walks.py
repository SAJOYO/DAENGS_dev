"""routers/walk.py + services/walk.py — 산책 기록.

DB 는 쓰지 않습니다. `fakes.py` 가 리포지토리를 바꿔치기하므로 여기서 보는 것은
**규칙**입니다 — 다시 올려도 한 건인가, 남의 기록에 손댈 수 있는가, 좌표가 순서대로
돌아오는가, 남의 강아지를 붙일 수 있는가.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeAdmin, FakeAppUser, FakePet, FakeWalk, Store, install
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
from daengs_backend.routers import walk as walk_router

OWNER = uuid.uuid4()
STRANGER = uuid.uuid4()

STARTED = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)
ENDED = STARTED + timedelta(minutes=32)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    s = install(Store(FakeAdmin()), monkeypatch)
    s.add_app_user(FakeAppUser(kakao_id=1, id=OWNER))
    return s


@pytest.fixture
def client(store: Store) -> TestClient:
    """인증을 통과한 상태로 고정합니다. 토큰 검증은 test_app_auth 가 봅니다."""
    app = FastAPI()
    app.include_router(walk_router.router)
    app.dependency_overrides[
        next(iter(CurrentAppUser.__metadata__)).dependency
    ] = lambda: AppPrincipal(app_user_id=OWNER)
    return TestClient(app, raise_server_exceptions=False)


def body(session_id: uuid.UUID, *, pet_id: uuid.UUID | None = None) -> dict:
    return {
        "client_session_id": str(session_id),
        "pet_id": str(pet_id) if pet_id else None,
        "started_at": STARTED.isoformat(),
        "ended_at": ENDED.isoformat(),
        "weather_code": 61,
        "is_day": True,
        "temperature_c": "18.5",
        "points": [
            {
                "client_seq": 1,
                "chain_index": 0,
                "at": (STARTED + timedelta(seconds=3)).isoformat(),
                "lat": "37.497900",
                "lng": "127.027600",
                "accuracy_m": 8.0,
                "is_mock": False,
            },
            {
                "client_seq": 0,
                "chain_index": 0,
                "at": STARTED.isoformat(),
                "lat": "37.497800",
                "lng": "127.027500",
                "accuracy_m": 5.0,
                "is_mock": False,
            },
        ],
    }


def test_올린_산책이_그대로_돌아온다(client: TestClient) -> None:
    response = client.post("/app/walks", json=body(uuid.uuid4()))

    assert response.status_code == 201
    data = response.json()
    assert data["weather_code"] == 61
    assert data["temperature_c"] == "18.5"
    # **좌표는 client_seq 순서**입니다. 기기가 뒤섞어 보내도 순서를 서버가 잡습니다 —
    # 순서가 곧 지나온 길이라 뒤섞이면 경로가 엉킵니다.
    assert [p["client_seq"] for p in data["points"]] == [0, 1]


def test_같은_산책을_두_번_올려도_한_건이다(client: TestClient, store: Store) -> None:
    """앱은 네트워크가 끊기면 다음에 다시 올립니다. 그때 두 건이 되면 안 됩니다."""
    session_id = uuid.uuid4()
    first = client.post("/app/walks", json=body(session_id))
    second = client.post("/app/walks", json=body(session_id))

    assert first.status_code == 201
    # 두 번째는 새로 만든 게 아니라 있던 것입니다.
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert len(store.walks) == 1
    assert len(store.walks[0].points) == 2


def test_남의_산책은_404(client: TestClient, store: Store) -> None:
    """403 으로 나누면 "그 id 는 존재한다"를 알려 주는 셈입니다."""
    other = FakeWalk(
        app_user_id=STRANGER,
        client_session_id=uuid.uuid4(),
        started_at=STARTED,
        ended_at=ENDED,
    )
    store.walks.append(other)

    assert client.get(f"/app/walks/{other.id}").status_code == 404


def test_목록은_최근_순이고_좌표가_없다(client: TestClient, store: Store) -> None:
    old = client.post("/app/walks", json=body(uuid.uuid4())).json()
    store.walks[-1].started_at = STARTED - timedelta(days=1)
    new = client.post("/app/walks", json=body(uuid.uuid4())).json()

    walks = client.get("/app/walks").json()["walks"]

    assert [w["id"] for w in walks] == [new["id"], old["id"]]
    # 목록에 좌표를 실으면 스무 건에 수만 점이 딸려 옵니다.
    assert "points" not in walks[0]


def test_내_강아지만_붙는다(client: TestClient, store: Store) -> None:
    """남의 pet_id 를 실어 보내도 그 강아지에 산책이 붙으면 안 됩니다.

    산책 자체는 사용자의 것이라 거절하지 않고 **강아지만 떼고** 저장합니다.
    """
    mine = FakePet(app_user_id=OWNER, name="네옹", breed="dog_beagle")
    theirs = FakePet(app_user_id=STRANGER, name="남의개", breed="dog_pug")
    store.pets += [mine, theirs]

    ok = client.post("/app/walks", json=body(uuid.uuid4(), pet_id=mine.id)).json()
    stolen = client.post("/app/walks", json=body(uuid.uuid4(), pet_id=theirs.id)).json()

    assert ok["pet_id"] == str(mine.id)
    assert stolen["pet_id"] is None


def test_끝이_시작보다_앞서면_422(client: TestClient) -> None:
    payload = body(uuid.uuid4())
    payload["ended_at"] = (STARTED - timedelta(minutes=1)).isoformat()

    assert client.post("/app/walks", json=payload).status_code == 422


def test_좌표_순번이_겹치면_422(client: TestClient) -> None:
    """겹친 채로 넣으면 DB 가 PK 위반으로 통째로 실패합니다. 여기서 이유를 말해 줍니다."""
    payload = body(uuid.uuid4())
    payload["points"][1]["client_seq"] = 1

    assert client.post("/app/walks", json=payload).status_code == 422


def test_날씨를_못_받은_산책도_올라간다(client: TestClient) -> None:
    """못 받은 것을 "맑음"으로 채우지 않습니다 — null 그대로 남습니다."""
    payload = body(uuid.uuid4())
    payload["weather_code"] = payload["is_day"] = payload["temperature_c"] = None

    data = client.post("/app/walks", json=payload).json()

    assert data["weather_code"] is None
    assert data["temperature_c"] is None
