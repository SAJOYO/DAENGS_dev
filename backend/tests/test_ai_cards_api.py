"""routers/ai_card.py — `/app/ai-cards/*` HTTP 경계 (#537, D-076).

서비스 규칙은 `test_ai_card_service.py` 가 본다. 여기서는 상태 코드·오류 코드·응답 모양과,
POST 202 → (백그라운드) → GET ready → bridge 로 PNG 를 받는 한 바퀴를 본다.
"""

import asyncio
import io
import uuid
from collections.abc import Iterator

import pytest
from cardimage_fakes import FakeEngine, FakeJudge
from fakes import FakeAdmin, FakeAppUser, FakeSession, Store, install
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr

from daengs_backend.config import settings
from daengs_backend.core import storage as storage_module
from daengs_backend.core.deps import AppPrincipal, current_app_member_token_only, current_app_user
from daengs_backend.core.storage import LocalBridgeStorage
from daengs_backend.repositories import app_user as app_user_repo
from daengs_backend.routers import ai_card as ai_card_router
from daengs_backend.services import ai_card as service
from daengs_backend.services import ai_card_engine
from daengs_cardimage.photo import MAX_PHOTO_BYTES

OWNER = uuid.uuid4()
JPEG = {"Content-Type": "image/jpeg"}


class _SessionFactory:
    def __call__(self):
        return self

    async def __aenter__(self):
        return FakeSession()

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    s = install(Store(FakeAdmin()), monkeypatch)
    s.add_app_user(FakeAppUser(kakao_id=1, id=OWNER))
    return s


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch, tmp_path) -> LocalBridgeStorage:
    s = LocalBridgeStorage(str(tmp_path), base_url="http://x")
    monkeypatch.setattr(storage_module, "get_storage", lambda: s)
    monkeypatch.setattr(service, "get_storage", lambda: s)
    return s


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> Iterator[list]:
    collected: list = []
    monkeypatch.setattr(service, "_spawn", collected.append)
    monkeypatch.setattr(service, "_session_factory", _SessionFactory())
    monkeypatch.setattr(settings, "cardimage_gemini_api_key", SecretStr("test-key"))
    monkeypatch.setattr(settings, "cardimage_months", frozenset({4, 9}))
    monkeypatch.setattr(settings, "cardimage_daily_limit", 1)
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: FakeEngine())
    monkeypatch.setattr(ai_card_engine, "default_judge", lambda: FakeJudge([4]))
    yield collected
    # 시작만 하고 안 돌린 코루틴을 그냥 두면 GC 때 RuntimeWarning 이 난다 — 여기서 닫아 정리한다.
    for job in collected:
        job.close()


@pytest.fixture
def client(store: Store, storage: LocalBridgeStorage, jobs: list) -> TestClient:
    app = FastAPI()
    app.include_router(ai_card_router.router)
    principal = AppPrincipal(app_user_id=OWNER)
    app.dependency_overrides[current_app_user] = lambda: principal
    # POST 는 토큰만 보는 문이다 — active 확인은 서비스가 가짜 `get_active_for_update` 로 한다.
    app.dependency_overrides[current_app_member_token_only] = lambda: principal
    return TestClient(app, raise_server_exceptions=False)


def _photo() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), (1, 2, 3)).save(buf, "JPEG")
    return buf.getvalue()


def _post(client: TestClient, **params):
    q = {"month": 4, "dog_name": "네오", **params}
    return client.post("/app/ai-cards", params=q, content=_photo(), headers=JPEG)


def _run_all(jobs: list) -> None:
    while jobs:
        asyncio.run(jobs.pop(0))


def test_한_바퀴(client: TestClient, jobs: list) -> None:
    r = _post(client)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "generating" and body["title"] == "BLOSSOM 네오"
    assert body["image_url"] is None

    _run_all(jobs)

    detail = client.get(f"/app/ai-cards/{body['id']}").json()
    assert detail["status"] == "ready"
    assert (detail["width"], detail["height"]) == (994, 1582)
    assert detail["likeness"] == 4 and detail["attempts"] == 1
    assert "/app/ai-cards/_bridge/download/" in detail["image_url"]

    png = client.get(detail["image_url"].removeprefix("http://x"))
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(png.content)).size == (994, 1582)


def test_list_has_no_image_urls(client: TestClient, jobs: list) -> None:
    _post(client)
    _run_all(jobs)
    cards = client.get("/app/ai-cards").json()["cards"]
    assert len(cards) == 1 and cards[0]["status"] == "ready" and cards[0]["image_url"] is None


def test_closed_month_is_404(client: TestClient) -> None:
    r = _post(client, month=12)
    assert r.status_code == 404 and r.json()["detail"]["code"] == "month_closed"


def test_bad_photo_is_400(client: TestClient) -> None:
    r = client.post("/app/ai-cards", params={"month": 4, "dog_name": "x"}, content=b"nope", headers=JPEG)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "undecodable"


def test_blank_name_is_400(client: TestClient) -> None:
    r = _post(client, dog_name="   ")
    assert r.status_code == 400 and r.json()["detail"]["code"] == "bad_name"


def test_too_large_is_413(client: TestClient) -> None:
    r = client.post(
        "/app/ai-cards", params={"month": 4, "dog_name": "x"}, content=b"a" * (MAX_PHOTO_BYTES + 1), headers=JPEG
    )
    assert r.status_code == 413 and r.json()["detail"]["code"] == "too_large"


def test_no_key_is_503_with_user_message(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cardimage_gemini_api_key", SecretStr(""))
    r = _post(client)
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert detail["code"] == "unavailable" and "DAENGS_" not in detail["message"]


def test_busy_is_409(client: TestClient) -> None:
    assert _post(client).status_code == 202
    r = _post(client)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_generating"


def test_daily_limit_is_429(client: TestClient, jobs: list) -> None:
    _post(client)
    _run_all(jobs)
    r = _post(client)
    assert r.status_code == 429 and r.json()["detail"]["code"] == "limit_reached"


def test_strangers_dog_is_404(client: TestClient) -> None:
    r = _post(client, dog_id=str(uuid.uuid4()))
    assert r.status_code == 404 and r.json()["detail"]["code"] == "dog_not_found"


def test_unknown_card_is_404(client: TestClient) -> None:
    assert client.get(f"/app/ai-cards/{uuid.uuid4()}").status_code == 404
    assert client.delete(f"/app/ai-cards/{uuid.uuid4()}").status_code == 404


def test_delete_is_204_then_404(client: TestClient, jobs: list) -> None:
    card_id = _post(client).json()["id"]
    _run_all(jobs)
    assert client.delete(f"/app/ai-cards/{card_id}").status_code == 204
    assert client.get(f"/app/ai-cards/{card_id}").status_code == 404


def _dependency_names(route: APIRoute) -> set[str]:
    names: set[str] = set()
    stack = [route.dependant]
    while stack:
        dependant = stack.pop()
        if dependant.call is not None:
            names.add(getattr(dependant.call, "__name__", repr(dependant.call)))
        stack.extend(dependant.dependencies)
    return names


def _route(path: str, method: str) -> APIRoute:
    for route in ai_card_router.router.routes:
        if isinstance(route, APIRoute) and route.path == path and method in route.methods:
            return route
    raise AssertionError(f"route not found: {method} {path}")


def test_post_uses_token_only_auth() -> None:
    """`CurrentAppUser` 로 되돌리면 20MB 본문을 받는 동안 `app_users FOR UPDATE` 를 쥔다."""
    names = _dependency_names(_route("/app/ai-cards", "POST"))
    assert "current_app_member_token_only" in names
    assert "current_app_user" not in names
    # 본문이 없는 짧은 요청은 그대로 요청 경계에서 active 를 본다.
    for path, method in [("/app/ai-cards", "GET"), ("/app/ai-cards/{card_id}", "GET"), ("/app/ai-cards/{card_id}", "DELETE")]:
        assert "current_app_user" in _dependency_names(_route(path, method)), (path, method)


def test_withdrawn_user_is_401(client: TestClient, store: Store, jobs: list, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _not_active(session, app_user_id):
        return None

    monkeypatch.setattr(app_user_repo, "get_active_for_update", _not_active)
    r = _post(client)
    assert r.status_code == 401
    assert r.json()["detail"] == {"code": "not_active", "message": "다시 로그인해 주세요."}
    assert store.ai_cards == [] and jobs == []


def test_bridge_unknown_key_is_404(client: TestClient) -> None:
    assert client.get(f"/app/ai-cards/_bridge/download/ai-cards/{OWNER}/{uuid.uuid4()}.png").status_code == 404
