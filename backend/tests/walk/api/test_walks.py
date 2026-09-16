"""routers/walk.py + services/walk.py — 산책 기록.

DB 는 쓰지 않습니다. `fakes.py` 가 리포지토리를 바꿔치기하므로 여기서 보는 것은
**규칙**입니다 — 다시 올려도 한 건인가, 남의 기록에 손댈 수 있는가, 좌표가 순서대로
돌아오는가, 남의 강아지를 붙일 수 있는가.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeAdmin, FakeAppUser, FakePet, FakeSession, FakeWalk, Store, install
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
from daengs_backend.orchestration.adapters.life import WalkWeatherObservation
from daengs_backend.routers import walk as walk_router
from daengs_backend.schemas.walk import WalkFinalizeRequest
from daengs_backend.services.walk_session import lifecycle as walk_service

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
    app.dependency_overrides[next(iter(CurrentAppUser.__metadata__)).dependency] = lambda: (
        AppPrincipal(app_user_id=OWNER)
    )
    app.dependency_overrides[walk_router.get_walk_weather_lookup] = lambda: None
    return TestClient(app, raise_server_exceptions=False)


def body(session_id: uuid.UUID, *, pet_ids: list[uuid.UUID] | None = None) -> dict:
    return {
        "client_session_id": str(session_id),
        "pet_ids": [str(p) for p in pet_ids or []],
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
    # 재시도 응답도 첫 응답과 같은 상세 계약입니다. 기존 Walk를 찾기만 하고
    # points를 미리 읽지 않으면 실제 async DB에서 응답 직렬화가 500으로 터집니다.
    assert second.json()["points"] == first.json()["points"]
    assert len(store.walks) == 1
    # 좌표는 **묶음**으로 담긴다. 세는 것은 묶음이 아니라 그 안의 점이다 —
    # 이 테스트가 보는 것은 "다시 올려도 좌표가 안 늘어난다" 이기 때문이다.
    assert sum(chunk.point_count for chunk in store.walks[0].points) == 2


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

    산책 자체는 사용자의 것이라 거절하지 않고 **그 아이만 떼고** 저장합니다.
    """
    mine = FakePet(app_user_id=OWNER, name="네옹", breed="dog_beagle")
    theirs = FakePet(app_user_id=STRANGER, name="남의개", breed="dog_pug")
    store.pets += [mine, theirs]

    ok = client.post("/app/walks", json=body(uuid.uuid4(), pet_ids=[mine.id])).json()
    mixed = client.post("/app/walks", json=body(uuid.uuid4(), pet_ids=[mine.id, theirs.id])).json()
    stolen = client.post("/app/walks", json=body(uuid.uuid4(), pet_ids=[theirs.id])).json()

    assert ok["pet_ids"] == [str(mine.id)]
    # 섞어 보내도 남의 아이는 빠지고, 산책은 거절되지 않습니다.
    assert mixed["pet_ids"] == [str(mine.id)]
    assert stolen["pet_ids"] == []


def test_여러_마리를_데리고_나간다(client: TestClient, store: Store) -> None:
    """두 마리를 데리고 나갔으면 **둘 다** 붙어야 합니다.

    한 아이만 남으면 나중에 챗봇이 나머지 아이의 운동량을 통째로 못 봅니다.
    """
    neong = FakePet(app_user_id=OWNER, name="네옹", breed="dog_beagle")
    dang = FakePet(app_user_id=OWNER, name="댕댕", breed="dog_pug")
    store.pets += [neong, dang]

    walk = client.post("/app/walks", json=body(uuid.uuid4(), pet_ids=[neong.id, dang.id])).json()

    assert set(walk["pet_ids"]) == {str(neong.id), str(dang.id)}
    # 순서는 다시 읽어도 같아야 합니다 — 뒤바뀌면 앱이 "바뀌었다" 로 읽습니다.
    again = client.get(f"/app/walks/{walk['id']}").json()
    assert again["pet_ids"] == walk["pet_ids"]


def test_같은_아이를_두_번_적어도_한_마리다(client: TestClient, store: Store) -> None:
    """조인 행의 PK 가 (walk_id, pet_id) 라 겹치면 DB 가 500 으로 터집니다."""
    neong = FakePet(app_user_id=OWNER, name="네옹", breed="dog_beagle")
    store.pets.append(neong)

    walk = client.post("/app/walks", json=body(uuid.uuid4(), pet_ids=[neong.id, neong.id])).json()

    assert walk["pet_ids"] == [str(neong.id)]


def test_아무도_안_골라도_산책은_기록된다(client: TestClient) -> None:
    """강아지를 등록하기 전에 걸었거나 고르지 않고 나선 경우입니다.

    **사람이 걸은 것은 걸은 것입니다.** 빈 목록을 거절하면 그 산책이 영영 안 올라갑니다.
    """
    walk = client.post("/app/walks", json=body(uuid.uuid4())).json()

    assert walk["pet_ids"] == []


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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("weather_code", -1),
        ("weather_code", 100),
        ("temperature_c", "-100.1"),
        ("temperature_c", "100.1"),
    ],
)
def test_capsule이_읽을_수_없는_날씨는_업로드에서_거절한다(
    client: TestClient,
    field: str,
    value: object,
) -> None:
    payload = body(uuid.uuid4())
    payload[field] = value

    assert client.post("/app/walks", json=payload).status_code == 422


def point(seq: int) -> dict:
    return {
        "client_seq": seq,
        "chain_index": 0,
        "at": (STARTED + timedelta(seconds=seq)).isoformat(),
        "lat": "37.497900",
        "lng": "127.027600",
        "accuracy_m": 8.0,
        "is_mock": False,
    }


def finalize_body(count: int) -> dict:
    return {
        "expected_point_count": count,
        "terminal_client_seq": count - 1 if count else None,
    }


def test_좌표를_나눠_올릴_수_있다(client: TestClient) -> None:
    """두 시간 산책이면 좌표가 5천 점이라 한 번에 보내면 바디 한도에 걸립니다."""
    created = client.post("/app/walks", json=body(uuid.uuid4())).json()

    response = client.post(
        f"/app/walks/{created['id']}/points",
        json={"points": [point(2), point(3)]},
    )

    assert response.status_code == 200
    assert [p["client_seq"] for p in response.json()["points"]] == [0, 1, 2, 3]


def test_같은_묶음을_다시_보내도_안_늘어난다(client: TestClient) -> None:
    """앱이 응답을 못 받고 다시 보내는 것은 재시도지 오류가 아닙니다."""
    created = client.post("/app/walks", json=body(uuid.uuid4())).json()
    payload = {"points": [point(2), point(3)]}

    client.post(f"/app/walks/{created['id']}/points", json=payload)
    again = client.post(f"/app/walks/{created['id']}/points", json=payload)

    assert again.status_code == 200
    assert [p["client_seq"] for p in again.json()["points"]] == [0, 1, 2, 3]


def test_남의_산책에는_좌표를_못_붙인다(client: TestClient, store: Store) -> None:
    other = FakeWalk(
        app_user_id=STRANGER,
        client_session_id=uuid.uuid4(),
        started_at=STARTED,
        ended_at=ENDED,
    )
    store.walks.append(other)

    response = client.post(f"/app/walks/{other.id}/points", json={"points": [point(0)]})

    assert response.status_code == 404
    assert other.points == []


def test_finalize는_계산과_봉인을_한번에_저장한다(client: TestClient, store: Store) -> None:
    created = client.post("/app/walks", json=body(uuid.uuid4())).json()

    response = client.post(
        f"/app/walks/{created['id']}/finalize",
        json=finalize_body(2),
    )

    assert response.status_code == 201
    result = response.json()
    assert result["walk_id"] == created["id"]
    assert result["analysis_state"] == "derived"
    assert result["point_count"] == 2
    assert result["terminal_client_seq"] == 1
    assert result["input_fingerprint"].startswith("sha256:")
    assert len(store.walk_analyses) == 1
    assert store.walks[0].analysis_state == "derived"
    assert len(store.walk_analyses[0].cellophane_sheets) == 1
    capsule = store.walk_analyses[0].capsule
    assert capsule is not None
    assert capsule.trail_context["status"] == "partial"
    assert capsule.trail_context["weather_code"] == 61
    assert [item["name"] for item in capsule.capabilities] == ["low_motion", "gap"]


def test_finalize는_대표_좌표의_KMA_관측으로_context를_한번만_보강한다(
    client: TestClient,
    store: Store,
) -> None:
    calls: list[tuple[float, float, datetime]] = []

    async def observed(lat: float, lon: float, at: datetime) -> WalkWeatherObservation:
        calls.append((lat, lon, at))
        return WalkWeatherObservation(
            status="captured",
            provider="kma-vilage-fcst:ncst",
            observed_at=STARTED.replace(minute=0),
            temperature_c=17.2,
            humidity_pct=73,
            precipitation_kind="rain",
            precipitation_mm=1.5,
        )

    client.app.dependency_overrides[walk_router.get_walk_weather_lookup] = lambda: observed
    created = client.post("/app/walks", json=body(uuid.uuid4())).json()
    url = f"/app/walks/{created['id']}/finalize"

    first = client.post(url, json=finalize_body(2))
    retried = client.post(url, json=finalize_body(2))

    assert first.status_code == 201
    assert retried.status_code == 200
    assert calls == [(37.4979, 127.0276, STARTED + timedelta(seconds=3))]
    context = store.walk_analyses[0].capsule.trail_context
    assert context["context_version"] == 2
    assert context["weather_code"] == 61
    assert context["is_day"] is True
    assert context["temperature_c"] == 17.2
    assert context["humidity_pct"] == 73
    assert context["precipitation_kind"] == "rain"
    assert context["precipitation_mm"] == 1.5
    assert context["provider"] == "kma-vilage-fcst:ncst+android_walk_upload_v1"


def test_KMA_실패는_앱_context로_finalize한다(
    client: TestClient,
    store: Store,
) -> None:
    async def fail(*_args):
        raise TimeoutError

    client.app.dependency_overrides[walk_router.get_walk_weather_lookup] = lambda: fail
    created = client.post("/app/walks", json=body(uuid.uuid4())).json()

    response = client.post(
        f"/app/walks/{created['id']}/finalize",
        json=finalize_body(2),
    )

    assert response.status_code == 201
    context = store.walk_analyses[0].capsule.trail_context
    assert context["status"] == "partial"
    assert context["weather_code"] == 61
    assert context["temperature_c"] == 18.5
    assert context["provider"] == "android_walk_upload_v1"


def test_앱_context도_없으면_KMA_실패_출처를_명시한다(
    client: TestClient,
    store: Store,
) -> None:
    async def failed(*_args) -> WalkWeatherObservation:
        return WalkWeatherObservation(
            status="failed",
            provider="kma-vilage-fcst:ncst",
            temperature_c=99,
            precipitation_kind="rain",
            failure_reason="게이트웨이 장애",
        )

    client.app.dependency_overrides[walk_router.get_walk_weather_lookup] = lambda: failed
    payload = body(uuid.uuid4())
    payload["weather_code"] = None
    payload["is_day"] = None
    payload["temperature_c"] = None
    created = client.post("/app/walks", json=payload).json()

    response = client.post(
        f"/app/walks/{created['id']}/finalize",
        json=finalize_body(2),
    )

    assert response.status_code == 201
    context = store.walk_analyses[0].capsule.trail_context
    assert context["status"] == "failed"
    assert context["provider"] == "kma-vilage-fcst:ncst"
    assert context["failure_reason"] == "게이트웨이 장애"
    assert context["temperature_c"] is None
    assert context["precipitation_kind"] is None


def test_같은_finalize_재시도는_기존_분석을_돌려준다(client: TestClient, store: Store) -> None:
    created = client.post("/app/walks", json=body(uuid.uuid4())).json()
    url = f"/app/walks/{created['id']}/finalize"

    first = client.post(url, json=finalize_body(2))
    retried = client.post(url, json=finalize_body(2))

    assert first.status_code == 201
    assert retried.status_code == 200
    assert retried.json()["analysis_id"] == first.json()["analysis_id"]
    assert len(store.walk_analyses) == 1


def test_finalize_후에는_좌표를_더할_수_없다(client: TestClient) -> None:
    created = client.post("/app/walks", json=body(uuid.uuid4())).json()
    client.post(
        f"/app/walks/{created['id']}/finalize",
        json=finalize_body(2),
    )

    response = client.post(
        f"/app/walks/{created['id']}/points",
        json={"points": [point(2), point(3)]},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "walk_already_finalized"


def test_불완전한_좌표열은_finalize하지_않고_상태를_보존한다(
    client: TestClient, store: Store
) -> None:
    created = client.post("/app/walks", json=body(uuid.uuid4())).json()

    response = client.post(
        f"/app/walks/{created['id']}/finalize",
        json=finalize_body(3),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "point_count_mismatch"
    assert store.walks[0].analysis_state == "collecting"
    assert store.walk_analyses == []


def test_다른_fingerprint는_봉인하지_않는다(client: TestClient, store: Store) -> None:
    created = client.post("/app/walks", json=body(uuid.uuid4())).json()
    manifest = finalize_body(2)
    manifest["input_fingerprint"] = "sha256:" + "0" * 64

    response = client.post(
        f"/app/walks/{created['id']}/finalize",
        json=manifest,
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "input_fingerprint_mismatch"
    assert store.walks[0].analysis_state == "collecting"
    assert store.walk_analyses == []


def test_봉인_상태에_분석이_없으면_충돌을_알린다(client: TestClient, store: Store) -> None:
    created = client.post("/app/walks", json=body(uuid.uuid4())).json()
    store.walks[0].analysis_state = "derived"

    response = client.post(
        f"/app/walks/{created['id']}/finalize",
        json=finalize_body(2),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "finalized_analysis_not_found"


def test_배포_사이에_누락된_capsule은_재시도에서_복구한다(client: TestClient, store: Store) -> None:
    created = client.post("/app/walks", json=body(uuid.uuid4())).json()
    url = f"/app/walks/{created['id']}/finalize"
    first = client.post(url, json=finalize_body(2))
    assert first.status_code == 201
    store.walk_analyses[0].capsule = None

    response = client.post(url, json=finalize_body(2))

    assert response.status_code == 200
    assert response.json()["analysis_id"] == first.json()["analysis_id"]
    capsule = store.walk_analyses[0].capsule
    assert capsule is not None
    assert capsule.trail_context["provider"] == "legacy_walk_metadata_v1"
    assert capsule.sealed_at == store.walk_analyses[0].derived_at


def test_남의_산책은_finalize할_수_없다(client: TestClient, store: Store) -> None:
    other = FakeWalk(
        app_user_id=STRANGER,
        client_session_id=uuid.uuid4(),
        started_at=STARTED,
        ended_at=ENDED,
    )
    store.walks.append(other)

    response = client.post(
        f"/app/walks/{other.id}/finalize",
        json=finalize_body(0),
    )

    assert response.status_code == 404


async def test_finalize_commit_실패는_rollback한다(client: TestClient, store: Store) -> None:
    created = client.post("/app/walks", json=body(uuid.uuid4())).json()
    session = FakeSession()

    async def fail_commit() -> None:
        raise RuntimeError("commit failed")

    session.commit = fail_commit
    with pytest.raises(RuntimeError, match="commit failed"):
        await walk_service.finalize_walk(
            session,
            OWNER,
            uuid.UUID(created["id"]),
            WalkFinalizeRequest(**finalize_body(2)),
        )

    assert session.rollbacks == 1


async def test_capsule_조립_실패는_분석과_derived를_남기지_않는다(
    client: TestClient,
    store: Store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = client.post("/app/walks", json=body(uuid.uuid4())).json()
    session = FakeSession()

    def fail_capsule(*args, **kwargs):
        raise RuntimeError("capsule build failed")

    monkeypatch.setattr(walk_service, "build_capsule_model", fail_capsule)
    with pytest.raises(RuntimeError, match="capsule build failed"):
        await walk_service.finalize_walk(
            session,
            OWNER,
            uuid.UUID(created["id"]),
            WalkFinalizeRequest(**finalize_body(2)),
        )

    assert store.walks[0].analysis_state == "collecting"
    assert store.walk_analyses == []
    assert session.rollbacks == 1
