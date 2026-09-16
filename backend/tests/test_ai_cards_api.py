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
from fakes import FakeAdmin, FakeAppUser, FakePet, FakeSession, Store, install
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr

from daengs_backend.config import settings
from daengs_backend.core import storage as storage_module
from daengs_backend.core.deps import AppPrincipal, current_app_member_token_only, current_app_user
from daengs_backend.core.storage import LocalBridgeStorage
from daengs_backend.models import AiCard
from daengs_backend.repositories import app_user as app_user_repo
from daengs_backend.routers import ai_card as ai_card_router
from daengs_backend.services import ai_card as service
from daengs_backend.services import ai_card_engine
from daengs_cardimage.engine import EngineError
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
    # 이 파일의 기존 테스트는 모두 "카드 한 장" 세상(#537·#543)을 본다 — 여러 장(#572 Task 4)은
    # 아래 전용 테스트에서만 pick_count 를 따로 올린다.
    monkeypatch.setattr(settings, "cardimage_pick_count", 1)
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
    for path, method in [
        ("/app/ai-cards", "GET"),
        ("/app/ai-cards/{card_id}", "GET"),
        ("/app/ai-cards/{card_id}", "DELETE"),
        ("/app/ai-cards/{card_id}/choose", "POST"),
    ]:
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


# ── #543 제품 규칙 ──────────────────────────────────────────────────────


def test_title_name_goes_to_title_only(client: TestClient) -> None:
    r = _post(client, title_name="kong")
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["title"] == "BLOSSOM KONG" and body["dog_name"] == "네오"


def test_title_name_too_long_is_422(client: TestClient) -> None:
    assert _post(client, title_name="a" * 41).status_code == 422


def test_month_taken_is_409_with_dog_name(
    client: TestClient, store: Store, jobs: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "cardimage_daily_limit", 0)
    pet = FakePet(app_user_id=OWNER, name="네오", breed="mix")
    store.pets.append(pet)
    assert _post(client, dog_id=str(pet.id)).status_code == 202
    _run_all(jobs)
    r = _post(client, dog_id=str(pet.id))
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "month_taken", "message": "네오는 이미 4월 카드가 있어요."}


def test_delete_does_not_give_limit_back(client: TestClient, jobs: list) -> None:
    card_id = _post(client).json()["id"]
    _run_all(jobs)
    assert client.delete(f"/app/ai-cards/{card_id}").status_code == 204
    r = _post(client)
    assert r.status_code == 429 and r.json()["detail"]["code"] == "limit_reached"


def test_list_has_daily_remaining(client: TestClient, jobs: list) -> None:
    before = client.get("/app/ai-cards").json()
    assert (before["daily_limit"], before["daily_remaining"]) == (1, 1)
    _post(client)
    _run_all(jobs)
    after = client.get("/app/ai-cards").json()
    assert (after["daily_limit"], after["daily_remaining"]) == (1, 0)


def test_list_unlimited_is_null(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cardimage_daily_limit", 0)
    body = client.get("/app/ai-cards").json()
    assert body["daily_limit"] is None and body["daily_remaining"] is None


@pytest.mark.parametrize(
    ("name", "expected"),
    [("네오", "네오는"), ("콩", "콩은"), ("KONG", "KONG은(는)"), ("보리 2", "보리 2은(는)")],
)
def test_with_topic(name: str, expected: str) -> None:
    assert ai_card_router._with_topic(name) == expected


# ── #572 Task 4 — 한 요청에 2장, 고른 한 장만 저장 ──────────────────────


def test_post_response_has_pick_group_and_progress(client: TestClient, jobs: list, monkeypatch) -> None:
    monkeypatch.setattr(settings, "cardimage_pick_count", 2)
    body = _post(client).json()
    assert body["pick_group"] is not None
    assert (body["done"], body["total"], body["finished"]) == (0, 2, False)


def test_get_reports_done_and_total_after_generation(client: TestClient, jobs: list, monkeypatch) -> None:
    monkeypatch.setattr(settings, "cardimage_pick_count", 2)
    card_id = _post(client).json()["id"]
    _run_all(jobs)
    detail = client.get(f"/app/ai-cards/{card_id}").json()
    assert detail["status"] == "ready"
    assert (detail["done"], detail["total"], detail["finished"]) == (2, 2, True)


def test_get_reports_finished_true_after_second_card_fails(
    client: TestClient, jobs: list, monkeypatch
) -> None:
    """`finished` 는 `done == total` 이 아니라 "더 만들 카드가 없다" 를 본다(fix round 1 Important 1)."""
    monkeypatch.setattr(settings, "cardimage_pick_count", 2)

    class _FailSecondCallEngine(FakeEngine):
        def generate(self, *, template_png, photo_jpeg, prompt, seed=None):
            self.calls.append({"template": template_png, "photo": photo_jpeg, "prompt": prompt, "seed": seed})
            if len(self.calls) == 2:
                raise EngineError("upstream", "두 번째 호출 실패")
            return self.outputs[0]

    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: _FailSecondCallEngine())
    card_id = _post(client).json()["id"]
    interim = client.get(f"/app/ai-cards/{card_id}").json()
    assert interim["finished"] is False

    _run_all(jobs)

    detail = client.get(f"/app/ai-cards/{card_id}").json()
    assert (detail["done"], detail["total"], detail["finished"]) == (1, 2, True)


def test_list_shows_both_cards_from_one_request(client: TestClient, jobs: list, monkeypatch) -> None:
    monkeypatch.setattr(settings, "cardimage_pick_count", 2)
    body = _post(client).json()
    _run_all(jobs)
    cards = client.get("/app/ai-cards").json()["cards"]
    assert len(cards) == 2
    assert {c["pick_group"] for c in cards} == {body["pick_group"]}


def test_choose_keeps_the_picked_card_and_deletes_its_siblings(client: TestClient, jobs: list, monkeypatch) -> None:
    monkeypatch.setattr(settings, "cardimage_pick_count", 2)
    posted = _post(client).json()
    _run_all(jobs)
    assert len(client.get("/app/ai-cards").json()["cards"]) == 2
    picked_id = posted["id"]

    resp = client.post(f"/app/ai-cards/{picked_id}/choose")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == picked_id

    remaining = client.get("/app/ai-cards").json()["cards"]
    assert [c["id"] for c in remaining] == [picked_id]


def test_choose_without_siblings_keeps_the_single_card(client: TestClient, jobs: list) -> None:
    """`pick_count=1`(기존 기본 경로)이면 형제가 없다 — 고른 카드를 그대로 돌려준다."""
    card_id = _post(client).json()["id"]
    _run_all(jobs)
    resp = client.post(f"/app/ai-cards/{card_id}/choose")
    assert resp.status_code == 200
    assert [c["id"] for c in client.get("/app/ai-cards").json()["cards"]] == [card_id]


def test_choose_a_failed_card_is_409_and_deletes_nothing(
    client: TestClient, store: Store, jobs: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#572 Task 4 fix round 2 R2-3 — `failed` 카드를 고르면 `ready` 형제를 지워 버릴 수 있다."""
    monkeypatch.setattr(settings, "cardimage_pick_count", 2)

    class _FailSecondCallEngine(FakeEngine):
        def generate(self, *, template_png, photo_jpeg, prompt, seed=None):
            self.calls.append({"template": template_png, "photo": photo_jpeg, "prompt": prompt, "seed": seed})
            if len(self.calls) == 2:
                raise EngineError("upstream", "두 번째 호출 실패")
            return self.outputs[0]

    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: _FailSecondCallEngine())
    _post(client)
    _run_all(jobs)
    cards = client.get("/app/ai-cards").json()["cards"]
    assert len(cards) == 2
    failed = next(c for c in cards if c["status"] == "failed")

    resp = client.post(f"/app/ai-cards/{failed['id']}/choose")

    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "not_ready"
    assert len(client.get("/app/ai-cards").json()["cards"]) == 2  # 아무것도 안 지워졌다


def test_choose_a_generating_card_is_409_and_deletes_nothing(
    client: TestClient, store: Store, jobs: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#572 Task 4 fix round 2 R2-3 — 아직 `generating` 인 카드를 고르면 이미 `ready` 인
    형제를 지워 버릴 수 있다."""
    monkeypatch.setattr(settings, "cardimage_pick_count", 2)
    posted = _post(client)
    primary_id = uuid.UUID(posted.json()["id"])
    sibling = next(c for c in store.ai_cards if c.id != primary_id)
    assert sibling.status == "generating"

    resp = client.post(f"/app/ai-cards/{sibling.id}/choose")

    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "not_ready"
    assert len(store.ai_cards) == 2  # 아무것도 안 지워졌다


def test_choose_rejects_a_card_that_belongs_to_another_user(client: TestClient, store: Store) -> None:
    """없는 것과 남의 것은 같은 404 다 (`_not_found`)."""
    other_card = AiCard(
        id=uuid.uuid4(), app_user_id=uuid.uuid4(), month=4, dog_name="남", title="BLOSSOM 남",
        status="ready", storage_key="ai-cards/other/x.png", generation="g", size_bytes=1, width=994, height=1582,
    )
    store.ai_cards.append(other_card)
    resp = client.post(f"/app/ai-cards/{other_card.id}/choose")
    assert resp.status_code == 404


def test_choose_unknown_card_is_404(client: TestClient) -> None:
    assert client.post(f"/app/ai-cards/{uuid.uuid4()}/choose").status_code == 404
