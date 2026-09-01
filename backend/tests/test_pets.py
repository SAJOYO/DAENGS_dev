"""routers/pet.py + services/pet.py — 강아지 프로필.

DB 는 쓰지 않습니다. `fakes.py` 가 리포지토리를 바꿔치기하므로 여기서 보는 것은
**규칙**입니다 — 첫 아이가 대표가 되는가, 남의 강아지에 손댈 수 있는가, 대표를
지우면 누가 승계하는가, 상한이 걸리는가.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
from daengs_backend.routers import pet as pet_router
from daengs_backend.services import pet as pet_service
from fakes import FakeAdmin, FakeAppUser, FakePet, FakeWalk, FakeWalkPet, Store, install

OWNER = uuid.uuid4()
STRANGER = uuid.uuid4()


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    s = install(Store(FakeAdmin()), monkeypatch)
    s.add_app_user(FakeAppUser(kakao_id=1, id=OWNER))
    return s


@pytest.fixture
def client(store: Store) -> TestClient:
    """인증을 통과한 상태로 고정합니다. 토큰 검증은 test_app_auth 가 봅니다."""
    app = FastAPI()
    app.include_router(pet_router.router)
    app.dependency_overrides[
        next(iter(CurrentAppUser.__metadata__)).dependency
    ] = lambda: AppPrincipal(app_user_id=OWNER)
    return TestClient(app, raise_server_exceptions=False)


def _body(name: str = "네옹", **kw: object) -> dict:
    return {"name": name, "breed": "toy_poodle_light_brown", **kw}


def test_첫_아이는_자동으로_대표가_된다(client: TestClient, store: Store) -> None:
    """고르라고 묻지 않습니다 — 고를 것이 없습니다."""
    r = client.post("/app/pets", json=_body())
    assert r.status_code == 201, r.text
    assert r.json()["is_primary"] is True
    assert store.app_users[1].primary_pet_id == uuid.UUID(r.json()["id"])


def test_두_마리째는_대표가_안_된다(client: TestClient, store: Store) -> None:
    first = client.post("/app/pets", json=_body("네옹")).json()
    second = client.post("/app/pets", json=_body("두찌")).json()
    assert second["is_primary"] is False
    assert store.app_users[1].primary_pet_id == uuid.UUID(first["id"])


def test_상한을_넘으면_409(client: TestClient) -> None:
    for i in range(pet_service.MAX_PETS_PER_USER):
        assert client.post("/app/pets", json=_body(f"개{i}")).status_code == 201
    r = client.post("/app/pets", json=_body("한마리더"))
    assert r.status_code == 409
    assert str(pet_service.MAX_PETS_PER_USER) in r.json()["detail"]


def test_남의_강아지는_못_건드린다(client: TestClient, store: Store) -> None:
    """**404 다. 403 이 아니다.**

    403 으로 나누면 "그 id 는 존재한다"를 알려 주는 셈이다.
    """
    theirs = FakePet(app_user_id=STRANGER, name="남의개", breed="beagle")
    store.pets.append(theirs)

    assert client.put(f"/app/pets/{theirs.id}", json=_body()).status_code == 404
    assert client.delete(f"/app/pets/{theirs.id}").status_code == 404
    assert client.put("/app/pets/primary", json={"pet_id": str(theirs.id)}).status_code == 404


def test_대표를_지우면_먼저_등록한_아이가_승계한다(client: TestClient, store: Store) -> None:
    first = client.post("/app/pets", json=_body("첫째")).json()
    second = client.post("/app/pets", json=_body("둘째")).json()

    assert client.delete(f"/app/pets/{first['id']}").status_code == 204
    assert store.app_users[1].primary_pet_id == uuid.UUID(second["id"])


def test_마지막_한_마리를_지우면_대표가_없다(client: TestClient, store: Store) -> None:
    only = client.post("/app/pets", json=_body()).json()
    assert client.delete(f"/app/pets/{only['id']}").status_code == 204
    assert store.app_users[1].primary_pet_id is None


def test_대표를_바꾼다(client: TestClient, store: Store) -> None:
    client.post("/app/pets", json=_body("첫째"))
    second = client.post("/app/pets", json=_body("둘째")).json()

    assert client.put("/app/pets/primary", json={"pet_id": second["id"]}).status_code == 204
    assert store.app_users[1].primary_pet_id == uuid.UUID(second["id"])


def test_primary_가_pet_id_로_안_먹힌다(client: TestClient) -> None:
    """`/app/pets/primary` 가 `update_pet` 으로 가면 안 된다.

    FastAPI 는 등록 순서대로 매칭하므로 `/{pet_id}` 를 먼저 선언하면 "primary" 를
    UUID 로 파싱하려다 422 가 난다. 실제로 그렇게 만들었다가 라우트 매칭을 재서
    찾았고, 이 테스트가 그 순서를 고정한다.
    """
    r = client.put("/app/pets/primary", json={"pet_id": str(uuid.uuid4())})
    assert r.status_code != 422, "primary 가 pet_id 로 파싱되고 있다"


def test_모름은_그대로_남는다(client: TestClient) -> None:
    """성별·중성화를 안 보내면 null 이다. **false 로 바뀌지 않는다.**"""
    r = client.post("/app/pets", json=_body()).json()
    assert r["sex"] is None
    assert r["neutered"] is None


def test_날짜와_종류는_같이_와야_한다(client: TestClient) -> None:
    """한쪽만 오면 422. 무슨 날인지 모르는 날짜는 안 받은 것만 못하다."""
    only_date = client.post("/app/pets", json=_body(birth_date="2024-03-01"))
    only_kind = client.post("/app/pets", json=_body(birth_date_kind="birthday"))
    assert only_date.status_code == 422
    assert only_kind.status_code == 422

    both = client.post(
        "/app/pets", json=_body(birth_date="2024-03-01", birth_date_kind="family_day")
    )
    assert both.status_code == 201
    assert both.json()["birth_date_kind"] == "family_day"


def test_목록은_상한을_같이_알려_준다(client: TestClient) -> None:
    """앱이 `+` 버튼을 언제 감출지 정하는 데 쓴다. 앱에 숫자를 박으면 갈라진다."""
    client.post("/app/pets", json=_body())
    r = client.get("/app/pets").json()
    assert r["max_pets"] == pet_service.MAX_PETS_PER_USER
    assert len(r["pets"]) == 1


def _walk(store: Store, *pet_ids: uuid.UUID) -> FakeWalk:
    """그 아이들과 나간 산책 하나를 store 에 둡니다."""
    walk = FakeWalk(
        app_user_id=OWNER,
        client_session_id=uuid.uuid4(),
        started_at=datetime(2026, 8, 31, 9, tzinfo=UTC),
        ended_at=datetime(2026, 8, 31, 10, tzinfo=UTC),
        pets=[FakeWalkPet(pet_id=p) for p in pet_ids],
    )
    store.walks.append(walk)
    return walk


def test_그_아이와만_나간_산책은_같이_지운다(client: TestClient, store: Store) -> None:
    """아이를 지웠는데 그 아이의 산책만 남으면 "누구와 갔는지 모르는 기록" 이 됩니다."""
    neong = client.post("/app/pets", json=_body("네옹")).json()
    solo = _walk(store, uuid.UUID(neong["id"]))

    assert client.delete(f"/app/pets/{neong['id']}").status_code == 204

    assert solo not in store.walks


def test_다른_아이와_같이_나간_산책은_남긴다(client: TestClient, store: Store) -> None:
    """그 산책은 **남은 아이의 기록이기도 합니다.**

    지우면 남은 아이의 운동량이 통째로 빕니다. 그 산책에서 지운 아이만 빠집니다.
    """
    neong = client.post("/app/pets", json=_body("네옹")).json()
    dang = client.post("/app/pets", json=_body("댕댕")).json()
    together = _walk(store, uuid.UUID(neong["id"]), uuid.UUID(dang["id"]))

    client.delete(f"/app/pets/{neong['id']}")

    assert together in store.walks
    assert together.pet_ids == [uuid.UUID(dang["id"])]


def test_아무도_안_붙은_산책은_건드리지_않는다(client: TestClient, store: Store) -> None:
    """강아지를 등록하기 전에 걸은 산책입니다. **사람이 걸은 것은 걸은 것입니다.**"""
    neong = client.post("/app/pets", json=_body("네옹")).json()
    alone = _walk(store)

    client.delete(f"/app/pets/{neong['id']}")

    assert alone in store.walks
