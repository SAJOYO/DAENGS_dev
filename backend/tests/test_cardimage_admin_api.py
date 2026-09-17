"""`/admin/cardimage/*` — 콘솔의 점검 경로 (#496, #592).

`test_admin_audit_view.py` 와 같은 방식으로 `current_admin` 을 오버라이드한다. `require()` 가
매번 새 함수를 만들어 `dependency_overrides[require(Perm.X)]` 는 키가 안 맞으므로, 그
아래에서 실제로 도는 `current_admin` 을 바꿔 끼운다.

#592 부터 이 경로가 DB 와 저장소를 본다. 표 대역은 `test_admin_card_store.py` 와 같이 **이
파일 안**에 있고(공용 `fakes.install` 에 넣으면 상관없는 테스트가 콘솔 표까지 들고 다닌다),
저장소는 임시 디렉터리 위의 진짜 `LocalBridgeStorage` 다 — 그래야 생성→목록→이미지→삭제
왕복이 실제로 바이트를 오간다.
"""

import base64
import io
import uuid

import pytest
from cardimage_fakes import FakeEngine, FakeJudge
from fakes import FakeSession
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from daengs_backend.config import settings
from daengs_backend.core.database import get_session
from daengs_backend.core.deps import Principal, current_admin
from daengs_backend.models import AdminAiCard
from daengs_backend.repositories import admin_ai_card as admin_ai_card_repo
from daengs_backend.routers import admin_cardimage
from daengs_backend.services import admin_card_store
from daengs_cardimage import catalog
from daengs_cardimage import engine as engine_mod
from daengs_cardimage.photo import MAX_PHOTO_BYTES


def _photo() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), (1, 2, 3)).save(buf, "JPEG")
    return buf.getvalue()


class Rows:
    """`admin_ai_cards` 표 대역. 진짜 정렬 규칙(최근 것부터)만 흉내 낸다."""

    def __init__(self) -> None:
        self.cards: list[AdminAiCard] = []


@pytest.fixture
def rows(monkeypatch: pytest.MonkeyPatch) -> Rows:
    store = Rows()

    def add(session, card):
        store.cards.append(card)
        return card

    async def get(session, card_id, *, for_update=False):
        return next((c for c in store.cards if c.id == card_id), None)

    async def list_recent(session, *, limit=50):
        return sorted(store.cards, key=lambda c: c.created_at, reverse=True)[:limit]

    async def delete(session, card):
        store.cards.remove(card)

    monkeypatch.setattr(admin_ai_card_repo, "add", add)
    monkeypatch.setattr(admin_ai_card_repo, "get", get)
    monkeypatch.setattr(admin_ai_card_repo, "list_recent", list_recent)
    monkeypatch.setattr(admin_ai_card_repo, "delete", delete)
    return store


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch, tmp_path):
    from daengs_backend.core.storage import LocalBridgeStorage

    s = LocalBridgeStorage(str(tmp_path), base_url="http://x")
    monkeypatch.setattr(admin_card_store, "get_storage", lambda: s)
    return s


@pytest.fixture(autouse=True)
def fake_engine(monkeypatch: pytest.MonkeyPatch) -> FakeEngine:
    """라우터는 고른 이름으로 엔진을 만든다(`engine_by_name`) — 그 자리 하나만 바꿔 끼우면
    실제 Gemini·GPU 서비스는 어느 테스트에서도 안 불린다. 다른 엔진이 필요한 테스트는
    그 뒤에 한 번 더 `setattr` 한다(나중 것이 이긴다)."""
    eng = FakeEngine()
    monkeypatch.setattr(admin_cardimage, "engine_by_name", lambda name: eng)
    monkeypatch.setattr(admin_cardimage, "default_judge", lambda: FakeJudge([4]))
    return eng


def _client(monkeypatch: pytest.MonkeyPatch, *, role: str = "ADMIN") -> TestClient:
    app = FastAPI()
    app.include_router(admin_cardimage.router)

    principal = Principal(admin_id=uuid.uuid4(), role=role)

    async def _fake_admin() -> Principal:
        return principal

    async def _fake_session() -> FakeSession:
        return FakeSession()

    app.dependency_overrides[current_admin] = _fake_admin
    # 표는 `rows` 대역이 가로채므로 세션은 commit 만 세는 가짜로 충분하다.
    app.dependency_overrides[get_session] = _fake_session
    # `settings.cardimage_dir` 는 이제 config.py 에서 저장소 절대 경로로 계산되므로
    # (#496 리뷰) 여기서 더 손댈 필요가 없다 — 실제 틀 폴더를 그대로 쓴다.
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, rows: Rows, storage) -> TestClient:
    return _client(monkeypatch)


def _post(client: TestClient, **params):
    args = {"card": "4", "dog_name": "네오"}
    args.update(params)
    return client.post(
        "/admin/cardimage/generate",
        params=args,
        content=_photo(),
        headers={"Content-Type": "image/jpeg"},
    )


def test_generate_returns_png_and_judge(client: TestClient) -> None:
    r = _post(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "BLOSSOM 네오"
    assert body["card"] == "4" and body["engine"] == "gemini"
    assert body["judge"]["likeness"] == 4
    assert body["attempts"] == 1
    # Nano Banana 2 는 seed 를 버린다 — 응답에 적어 두면 "다시 뽑을 수 있다"는 거짓말이 된다.
    assert body["seed"] is None
    assert body["stored"] is True and body["id"]
    assert Image.open(io.BytesIO(base64.b64decode(body["png_base64"]))).size == (994, 1582)


def test_mixed_case_content_type_is_accepted(client: TestClient) -> None:
    """`ALLOWED_MIME` 은 소문자 집합이다 — `.lower()` 없이는 이 헤더가 걸러진다."""
    r = client.post(
        "/admin/cardimage/generate",
        params={"card": "4", "dog_name": "네오"},
        content=_photo(),
        headers={"Content-Type": "Image/JPEG"},
    )
    assert r.status_code == 200, r.text


def test_strawberry_uses_the_face_only_prompt(
    monkeypatch: pytest.MonkeyPatch, rows: Rows, storage, fake_engine: FakeEngine
) -> None:
    client = _client(monkeypatch)
    r = _post(client, card="strawberry")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["card"] == "strawberry" and body["title"] == "BERRY 네오"
    prompt = fake_engine.calls[0]["prompt"]
    assert "only the dog's face is visible" in prompt
    assert rows.cards[0].card_key == "strawberry"


def test_closed_month_is_404(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # #572 부터 기본값이 1~12월 전부 열림이라 실제로 닫힌 달이 없다 — 이 테스트는 `require_open`
    # 이 여전히 404 로 막는지를 보는 것이 목적이라, 다른 테스트 파일들과 같은 방식으로
    # `cardimage_months` 를 좁혀 12월만 닫힌 상태를 만든다.
    monkeypatch.setattr(settings, "cardimage_months", frozenset({4, 9}))
    r = _post(client, card="12", dog_name="x")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "card_closed"


@pytest.mark.parametrize("card", ["13", "banana", "0"])
def test_unknown_card_is_404(client: TestClient, card: str) -> None:
    r = _post(client, card=card, dog_name="x")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "card_closed"


def test_bad_photo_is_400_with_code(client: TestClient) -> None:
    r = client.post(
        "/admin/cardimage/generate",
        params={"card": "4", "dog_name": "x"},
        content=b"nope",
        headers={"Content-Type": "image/jpeg"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "undecodable"


def test_engine_upstream_is_502(monkeypatch: pytest.MonkeyPatch, rows: Rows, storage) -> None:
    client = _client(monkeypatch)
    monkeypatch.setattr(
        admin_cardimage, "engine_by_name", lambda name: FakeEngine(error=engine_mod.EngineError("upstream", "x"))
    )
    r = _post(client, dog_name="x")
    assert r.status_code == 502


def test_no_key_is_503(monkeypatch: pytest.MonkeyPatch, rows: Rows, storage) -> None:
    client = _client(monkeypatch)
    monkeypatch.setattr(
        admin_cardimage, "engine_by_name", lambda name: FakeEngine(error=engine_mod.EngineError("no_key", "x"))
    )
    r = _post(client, dog_name="x")
    assert r.status_code == 503


def test_declared_too_large_is_413_before_reading_body(client: TestClient) -> None:
    """일반 `bytes` 본문은 `Content-Length` 가 실려 온다 — 그 값만으로 즉시 끊는다."""
    r = client.post(
        "/admin/cardimage/generate",
        params={"card": "4", "dog_name": "x"},
        content=b"a" * (MAX_PHOTO_BYTES + 1),
        headers={"Content-Type": "image/jpeg"},
    )
    assert r.status_code == 413
    assert r.json()["detail"]["code"] == "too_large"


def test_streamed_too_large_is_413_without_declared_length(client: TestClient) -> None:
    """제너레이터로 보내면 `Content-Length` 가 안 실린다 — 청크 누적 검사(스트리밍 갈래)를
    실제로 거치는지는 이 테스트로만 확인된다. httpx 의 `TestClient` 가 제너레이터 본문을
    받아 ASGI 로 여러 청크에 걸쳐 넘겨준다는 것은 별도로 확인했다."""

    def _chunks():
        yield b"a" * MAX_PHOTO_BYTES
        yield b"a"

    r = client.post(
        "/admin/cardimage/generate",
        params={"card": "4", "dog_name": "x"},
        content=_chunks(),
        headers={"Content-Type": "image/jpeg"},
    )
    assert r.status_code == 413
    assert r.json()["detail"]["code"] == "too_large"


def test_whitespace_only_dog_name_is_400_with_code(client: TestClient) -> None:
    r = _post(client, dog_name="   ")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "bad_name"


def test_missing_dog_name_is_422(client: TestClient) -> None:
    r = client.post(
        "/admin/cardimage/generate",
        params={"card": "4"},
        content=_photo(),
        headers={"Content-Type": "image/jpeg"},
    )
    assert r.status_code == 422


def test_viewer_lacks_search_inspect_is_403(monkeypatch: pytest.MonkeyPatch, rows: Rows, storage) -> None:
    """`VIEWER` 는 `search:inspect` 가 없다 (core/deps.py 의 ROLE_PERMISSIONS) — 403."""
    client = _client(monkeypatch, role="VIEWER")
    assert _post(client, dog_name="x").status_code == 403
    assert client.get("/admin/cardimage/options").status_code == 403
    assert client.get("/admin/cardimage/cards").status_code == 403


# ── 엔진 고르기 ──────────────────────────────────────────────────────────


def test_cardgen_without_url_is_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cardgen_url", "")
    r = _post(client, engine="cardgen")
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "cardgen_disabled"


def test_cardgen_passes_the_seed_through_and_records_it(
    monkeypatch: pytest.MonkeyPatch, rows: Rows, storage, fake_engine: FakeEngine
) -> None:
    monkeypatch.setattr(settings, "cardgen_url", "http://cardgen.example")
    client = _client(monkeypatch)
    r = _post(client, engine="cardgen", seed=7)
    assert r.status_code == 200, r.text
    assert fake_engine.calls[0]["seed"] == 7
    assert r.json()["seed"] == 7 and r.json()["engine"] == "cardgen"
    assert rows.cards[0].seed == 7 and rows.cards[0].engine == "cardgen"


def test_gemini_with_seed_is_400(client: TestClient) -> None:
    """Nano Banana 2 는 seed 를 버린다 — 조용히 무시하면 응답의 seed 가 거짓말이 된다."""
    r = _post(client, engine="gemini", seed=7)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "seed_not_supported"


def test_unknown_engine_is_422(client: TestClient) -> None:
    assert _post(client, engine="midjourney").status_code == 422


# ── 저장·목록·이미지·삭제 ─────────────────────────────────────────────────


def test_storage_off_still_returns_the_card(monkeypatch: pytest.MonkeyPatch, rows: Rows) -> None:
    """저장소가 꺼져 있어도 이미 돈이 나간 카드는 돌려준다 (spec ④)."""
    from daengs_backend.core.storage import NotConfiguredStorage

    monkeypatch.setattr(admin_card_store, "get_storage", lambda: NotConfiguredStorage())
    client = _client(monkeypatch)

    r = _post(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stored"] is False and body["id"] is None and body["png_base64"]
    assert rows.cards == []
    assert client.get("/admin/cardimage/cards").json()["cards"] == []


def test_generate_then_list_image_and_delete(client: TestClient) -> None:
    made = _post(client).json()

    listed = client.get("/admin/cardimage/cards")
    assert listed.status_code == 200, listed.text
    cards = listed.json()["cards"]
    assert len(cards) == 1
    row = cards[0]
    assert row["id"] == made["id"] and row["card"] == "4" and row["dog_name"] == "네오"
    assert row["engine"] == "gemini" and row["likeness"] == 4
    assert (row["width"], row["height"]) == (994, 1582)

    img = client.get(f"/admin/cardimage/cards/{made['id']}/image")
    assert img.status_code == 200
    assert img.headers["content-type"] == "image/png"
    assert img.content == base64.b64decode(made["png_base64"])

    assert client.delete(f"/admin/cardimage/cards/{made['id']}").status_code == 204
    assert client.get("/admin/cardimage/cards").json()["cards"] == []
    assert client.get(f"/admin/cardimage/cards/{made['id']}/image").status_code == 404
    assert client.delete(f"/admin/cardimage/cards/{made['id']}").status_code == 404


def test_missing_card_image_is_404(client: TestClient) -> None:
    assert client.get(f"/admin/cardimage/cards/{uuid.uuid4()}/image").status_code == 404


# ── 화면이 받아 가는 선택지 ───────────────────────────────────────────────


def test_options_lists_every_card_and_both_engines(client: TestClient) -> None:
    r = client.get("/admin/cardimage/options")
    assert r.status_code == 200, r.text
    body = r.json()
    keys = [c["key"] for c in body["cards"]]
    assert keys == [str(m) for m in range(1, 13)] + list(catalog.KINDS)
    assert len(keys) == 14
    assert body["cards"][3]["label"] == "4월 · BLOSSOM"
    assert body["cards"][12]["label"] == "딸기 · BERRY"
    assert [e["key"] for e in body["engines"]] == ["gemini", "cardgen"]
    assert [e["label"] for e in body["engines"]] == ["Nano Banana 2", "FLUX.2-klein-4B"]
    # 콘솔은 백엔드 문구를 복제하지 않는다 — 이 응답이 원본이다 (#592).
    assert body["photo_guidance"] == catalog.PHOTO_GUIDANCE


def test_options_says_why_cardgen_is_off(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cardgen_url", "")
    cardgen = client.get("/admin/cardimage/options").json()["engines"][1]
    assert cardgen["available"] is False and "DAENGS_CARDGEN_URL" in cardgen["reason"]

    monkeypatch.setattr(settings, "cardgen_url", "http://cardgen.example")
    cardgen = client.get("/admin/cardimage/options").json()["engines"][1]
    assert cardgen["available"] is True and cardgen["reason"] is None
