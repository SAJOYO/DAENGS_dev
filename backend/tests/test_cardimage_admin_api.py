"""`/admin/cardimage/generate` — 콘솔의 점검 경로, DB 는 안 봄 (#496).

`test_admin_audit_view.py` 와 같은 방식으로 `current_admin` 을 오버라이드한다. `require()` 가
매번 새 함수를 만들어 `dependency_overrides[require(Perm.X)]` 는 키가 안 맞으므로, 그
아래에서 실제로 도는 `current_admin` 을 바꿔 끼운다 — 라우터는 DB 를 보지 않으므로
`fakes.install(...)` 스토어는 필요 없다.
"""

import base64
import io
import uuid

import pytest
from cardimage_fakes import FakeEngine, FakeJudge
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from daengs_backend.core.deps import Principal, current_admin
from daengs_backend.routers import admin_cardimage
from daengs_backend.services.cardimage import engine as engine_mod
from daengs_backend.services.cardimage.photo import MAX_PHOTO_BYTES


def _photo() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), (1, 2, 3)).save(buf, "JPEG")
    return buf.getvalue()


def _client(monkeypatch: pytest.MonkeyPatch, *, role: str = "ADMIN") -> TestClient:
    app = FastAPI()
    app.include_router(admin_cardimage.router)

    principal = Principal(admin_id=uuid.uuid4(), role=role)

    async def _fake_admin() -> Principal:
        return principal

    app.dependency_overrides[current_admin] = _fake_admin
    # `settings.cardimage_dir` 는 이제 config.py 에서 저장소 절대 경로로 계산되므로
    # (#496 리뷰) 여기서 더 손댈 필요가 없다 — 실제 틀 폴더를 그대로 쓴다.
    monkeypatch.setattr(admin_cardimage, "default_engine", lambda: FakeEngine())
    monkeypatch.setattr(admin_cardimage, "default_judge", lambda: FakeJudge([4]))
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return _client(monkeypatch)


def test_generate_returns_png_and_judge(client: TestClient) -> None:
    r = client.post(
        "/admin/cardimage/generate",
        params={"month": 4, "dog_name": "네오"},
        content=_photo(),
        headers={"Content-Type": "image/jpeg"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "BLOSSOM 네오"
    assert body["judge"]["likeness"] == 4
    assert body["attempts"] == 1
    assert Image.open(io.BytesIO(base64.b64decode(body["png_base64"]))).size == (994, 1582)


def test_mixed_case_content_type_is_accepted(client: TestClient) -> None:
    """`ALLOWED_MIME` 은 소문자 집합이다 — `.lower()` 없이는 이 헤더가 걸러진다."""
    r = client.post(
        "/admin/cardimage/generate",
        params={"month": 4, "dog_name": "네오"},
        content=_photo(),
        headers={"Content-Type": "Image/JPEG"},
    )
    assert r.status_code == 200, r.text


def test_closed_month_is_404(client: TestClient) -> None:
    r = client.post(
        "/admin/cardimage/generate",
        params={"month": 12, "dog_name": "x"},   # 12월은 틀만 있고 아직 안 열렸다 (열린 달: 4·9)
        content=_photo(),
        headers={"Content-Type": "image/jpeg"},
    )
    assert r.status_code == 404


def test_bad_photo_is_400_with_code(client: TestClient) -> None:
    r = client.post(
        "/admin/cardimage/generate",
        params={"month": 4, "dog_name": "x"},
        content=b"nope",
        headers={"Content-Type": "image/jpeg"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "undecodable"


def test_engine_upstream_is_502(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        admin_cardimage, "default_engine", lambda: FakeEngine(error=engine_mod.EngineError("upstream", "x"))
    )
    r = client.post(
        "/admin/cardimage/generate",
        params={"month": 4, "dog_name": "x"},
        content=_photo(),
        headers={"Content-Type": "image/jpeg"},
    )
    assert r.status_code == 502


def test_no_key_is_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        admin_cardimage, "default_engine", lambda: FakeEngine(error=engine_mod.EngineError("no_key", "x"))
    )
    r = client.post(
        "/admin/cardimage/generate",
        params={"month": 4, "dog_name": "x"},
        content=_photo(),
        headers={"Content-Type": "image/jpeg"},
    )
    assert r.status_code == 503


def test_declared_too_large_is_413_before_reading_body(client: TestClient) -> None:
    """일반 `bytes` 본문은 `Content-Length` 가 실려 온다 — 그 값만으로 즉시 끊는다."""
    body = b"a" * (MAX_PHOTO_BYTES + 1)
    r = client.post(
        "/admin/cardimage/generate",
        params={"month": 4, "dog_name": "x"},
        content=body,
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
        params={"month": 4, "dog_name": "x"},
        content=_chunks(),
        headers={"Content-Type": "image/jpeg"},
    )
    assert r.status_code == 413
    assert r.json()["detail"]["code"] == "too_large"


def test_whitespace_only_dog_name_is_400_with_code(client: TestClient) -> None:
    r = client.post(
        "/admin/cardimage/generate",
        params={"month": 4, "dog_name": "   "},
        content=_photo(),
        headers={"Content-Type": "image/jpeg"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "bad_name"


def test_missing_dog_name_is_422(client: TestClient) -> None:
    r = client.post(
        "/admin/cardimage/generate",
        params={"month": 4},
        content=_photo(),
        headers={"Content-Type": "image/jpeg"},
    )
    assert r.status_code == 422


def test_viewer_lacks_search_inspect_is_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """`VIEWER` 는 `search:inspect` 가 없다 (core/deps.py 의 ROLE_PERMISSIONS) — 403."""
    client = _client(monkeypatch, role="VIEWER")
    r = client.post(
        "/admin/cardimage/generate",
        params={"month": 4, "dog_name": "x"},
        content=_photo(),
        headers={"Content-Type": "image/jpeg"},
    )
    assert r.status_code == 403
