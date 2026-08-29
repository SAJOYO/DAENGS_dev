"""`/dogs` — 등록·조회와 소유권 경계.

conftest.py 의 약속대로 **DB 에 붙지 않는다.** repositories.dog 를 dict 기반 대역으로
갈아 끼우고(서비스가 `dog_repo.<함수>` 로 부르므로 모듈 속성 교체가 잡힌다), 세션은
commit/rollback 만 받아 주는 껍데기다. SQL 이 맞는지는 여기서 알 수 없다 —
`db/migrations/2026-08-29_dogs.sql` 을 적용한 DB 에 `uv run dev` 로 붙여 확인한다.

여기서 보는 것: 문(앱 회원 전용) · 입력 검증 · **타 회원의 개 = 404 (존재 은닉)**.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from daengs_backend.core.database import get_session
from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import create_access_token
from daengs_backend.models import Dog
from daengs_backend.repositories import dog as dog_repo

OWNER = uuid.uuid4()
STRANGER = uuid.uuid4()


def _bearer(app_user_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(app_user_id, SubjectType.APP)}"}


class _Session:
    """트랜잭션 경계 호출만 받아 주는 껍데기. 저장은 아래 dict 대역이 한다."""

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass


@pytest.fixture
def dogs_store(monkeypatch: pytest.MonkeyPatch) -> dict[uuid.UUID, Dog]:
    store: dict[uuid.UUID, Dog] = {}

    async def create(session, **fields) -> Dog:
        dog = Dog(**fields)
        # DB 가 채우는 값(server_default)을 대역이 대신 채운다.
        dog.id = uuid.uuid4()
        dog.created_at = datetime.now(UTC)
        store[dog.id] = dog
        return dog

    async def get_by_id(session, dog_id: uuid.UUID) -> Dog | None:
        return store.get(dog_id)

    async def list_by_owner(session, app_user_id: uuid.UUID) -> list[Dog]:
        mine = [dog for dog in store.values() if dog.app_user_id == app_user_id]
        return sorted(mine, key=lambda dog: (dog.created_at, dog.id))

    monkeypatch.setattr(dog_repo, "create", create)
    monkeypatch.setattr(dog_repo, "get_by_id", get_by_id)
    monkeypatch.setattr(dog_repo, "list_by_owner", list_by_owner)
    return store


@pytest.fixture
def client(dogs_store) -> TestClient:
    from daengs_backend.main import app

    async def _fake_session():
        yield _Session()

    app.dependency_overrides[get_session] = _fake_session
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_session, None)


HALMAE = {
    "name": "할매",
    "breed": "말티즈",
    "birth_date": "2013-07-01",
    "sex": "F",
    "neutered": False,
    "weight_kg": 3.2,
    "size_class": "small",
}


def test_토큰_없이_부르면_401(client: TestClient) -> None:
    assert client.get("/dogs").status_code == 401
    assert client.post("/dogs", json=HALMAE).status_code == 401


def test_관리자_토큰은_앱_API_에_못_들어온다(client: TestClient) -> None:
    admin = {"Authorization": f"Bearer {create_access_token(uuid.uuid4(), SubjectType.ADMIN, 'ADMIN')}"}
    assert client.get("/dogs", headers=admin).status_code == 401


def test_등록하면_그대로_돌아오고_나이는_저장되지_않는다(client: TestClient) -> None:
    got = client.post("/dogs", json=HALMAE, headers=_bearer(OWNER))

    assert got.status_code == 201
    body = got.json()
    assert body["name"] == "할매"
    assert body["size_class"] == "small"
    assert body["birth_date"] == "2013-07-01"
    # 나이는 계약에 없다 — birth_date 에서 소비자가 계산한다 (스키마 docstring).
    assert "age" not in body and "age_years" not in body


def test_모르는_사실은_미상으로_남는다(client: TestClient) -> None:
    """size_class 만 필수다. 나머지를 지어내라고 강요하지 않는다."""
    got = client.post("/dogs", json={"name": "몽이", "size_class": "medium"},
                      headers=_bearer(OWNER))
    assert got.status_code == 201
    body = got.json()
    assert body["weight_kg"] is None
    assert body["birth_date"] is None


@pytest.mark.parametrize("bad", [
    {"name": "할매"},                                           # size_class 없음
    {**HALMAE, "size_class": "giant"},                          # 어휘 밖
    {**HALMAE, "weight_kg": 0},                                 # 경계 밖
    {**HALMAE, "name": "   "},                                  # 공백뿐
    {**HALMAE, "birth_date": str(date.today().replace(year=date.today().year + 1))},
    {**HALMAE, "age_years": 13},                                # 계약 밖 키 (extra=forbid)
])
def test_잘못된_입력은_422(client: TestClient, bad: dict) -> None:
    assert client.post("/dogs", json=bad, headers=_bearer(OWNER)).status_code == 422


def test_목록은_내_강아지만_등록_순서대로(client: TestClient) -> None:
    first = client.post("/dogs", json=HALMAE, headers=_bearer(OWNER)).json()
    second = client.post("/dogs", json={"name": "장군", "size_class": "large"},
                         headers=_bearer(OWNER)).json()
    client.post("/dogs", json={"name": "남의개", "size_class": "small"},
                headers=_bearer(STRANGER))

    got = client.get("/dogs", headers=_bearer(OWNER))

    assert got.status_code == 200
    assert [dog["id"] for dog in got.json()] == [first["id"], second["id"]]


def test_타_회원의_개는_없는_개와_같은_404(client: TestClient) -> None:
    """403 이면 "그 id 의 개가 존재한다"가 새어 나간다 — 존재를 숨긴다 (services docstring)."""
    dog = client.post("/dogs", json=HALMAE, headers=_bearer(OWNER)).json()

    mine = client.get(f"/dogs/{dog['id']}", headers=_bearer(OWNER))
    theirs = client.get(f"/dogs/{dog['id']}", headers=_bearer(STRANGER))
    unknown = client.get(f"/dogs/{uuid.uuid4()}", headers=_bearer(OWNER))

    assert mine.status_code == 200
    assert theirs.status_code == 404
    assert unknown.status_code == 404
    assert theirs.json() == unknown.json(), "남의 개와 없는 개의 응답이 달라선 안 된다"
