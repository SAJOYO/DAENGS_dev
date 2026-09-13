"""`/admin/cardimage/generate` — 콘솔의 점검 경로, DB 는 안 봄 (#496).

`test_admin_audit_view.py` 와 같은 방식으로 `current_admin` 을 오버라이드한다. `require()` 가
매번 새 함수를 만들어 `dependency_overrides[require(Perm.X)]` 는 키가 안 맞으므로, 그
아래에서 실제로 도는 `current_admin` 을 바꿔 끼운다 — 라우터는 DB 를 보지 않으므로
`fakes.install(...)` 스토어는 필요 없다.
"""

import base64
import io
import uuid
from pathlib import Path

import pytest
from cardimage_fakes import FakeEngine, FakeJudge
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from daengs_backend.config import settings
from daengs_backend.core.deps import Principal, current_admin
from daengs_backend.routers import admin_cardimage
from daengs_backend.services.cardimage import engine as engine_mod

#: 틀 12장·글꼴이 있는 실제 폴더. `settings.cardimage_dir` 기본값("cardimage")은 저장소
#: 루트 기준이라, backend/ 를 cwd 로 도는 pytest 에서는 그대로 두면 못 찾는다
#: (`test_cardimage_generate.py` 와 같은 계산).
CARDIMAGE = Path(__file__).resolve().parents[2] / "cardimage"


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
    monkeypatch.setattr(settings, "cardimage_dir", CARDIMAGE)
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


def test_closed_month_is_404(client: TestClient) -> None:
    r = client.post(
        "/admin/cardimage/generate",
        params={"month": 9, "dog_name": "x"},
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
