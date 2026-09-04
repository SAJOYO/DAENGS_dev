"""routers/dogcard.py + services/dogcard.py — **도감 카드** (`/app/cards/*`, D-052).

리포지토리는 `fakes.py` 가, 저장소는 임시 디렉터리 위의 **진짜** `LocalBridgeStorage`
가 맡습니다.

여기서 보는 것은 이 도메인만의 규칙입니다 — **id 를 앱이 만든다**는 데서 나오는 것들:
같은 카드를 두 번 올려도 한 장인가, 남이 가진 id 로 올리면 어떻게 되는가, 이미 뽑아
둔 카드가 다시 올린 값으로 조용히 바뀌지 않는가.
"""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from fakes import FakeAdmin, FakeAppUser, FakePet, Store, install
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.core import storage as storage_module
from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
from daengs_backend.core.storage import LocalBridgeStorage, NotConfiguredStorage
from daengs_backend.models import DogCard
from daengs_backend.routers import dogcard as card_router
from daengs_backend.services import dogcard as card_service

OWNER = uuid.uuid4()
STRANGER = uuid.uuid4()

PNG = "image/png"
DRAWN_AT = "2026-09-04T10:00:00Z"


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    s = install(Store(FakeAdmin()), monkeypatch)
    s.add_app_user(FakeAppUser(kakao_id=1, id=OWNER))
    return s


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch, tmp_path) -> LocalBridgeStorage:
    s = LocalBridgeStorage(str(tmp_path), base_url="http://x")
    monkeypatch.setattr(storage_module, "get_storage", lambda: s)
    monkeypatch.setattr(card_service, "get_storage", lambda: s)
    return s


@pytest.fixture
def client(store: Store, storage: LocalBridgeStorage) -> TestClient:
    app = FastAPI()
    app.include_router(card_router.router)
    app.dependency_overrides[
        next(iter(CurrentAppUser.__metadata__)).dependency
    ] = lambda: AppPrincipal(app_user_id=OWNER)
    return TestClient(app, raise_server_exceptions=False)


def _body(**kw) -> dict:
    return {
        "template_id": "cabbage",
        "dog_name": "네옹",
        "drawn_at": DRAWN_AT,
        "code_text": "0412",
        "user_framed": True,
        "core_left": 10,
        "core_top": 20,
        "core_right": 110,
        "core_bottom": 140,
        **kw,
    }


def _upload(client: TestClient, url: str, data: bytes, content_type: str = PNG):
    return client.put(
        url.removeprefix("http://x"),
        content=data,
        headers={"Content-Type": content_type},
    )


def _sync(client: TestClient, card_id: uuid.UUID, data: bytes = b"face-png", **kw) -> dict:
    """올리기 → 얼굴 PUT → confirm 한 바퀴."""
    r = client.put(f"/app/cards/{card_id}", json=_body(**kw))
    assert r.status_code in (200, 201), r.text
    got = r.json()
    if got["face_upload"] is not None:
        assert _upload(client, got["face_upload"]["upload_url"], data).status_code == 200
        assert client.post(f"/app/cards/{card_id}/face/confirm").status_code == 200
    return got


# ── id 를 앱이 만든다 ────────────────────────────────────────────────────


def test_한_바퀴(client, storage) -> None:
    card_id = uuid.uuid4()
    got = _sync(client, card_id)

    assert got["created"] is True
    assert got["card"]["id"] == str(card_id)
    key = got["face_upload"]["storage_key"]
    assert storage.local_path(key).read_bytes() == b"face-png"

    detail = client.get(f"/app/cards/{card_id}").json()
    assert detail["has_face"] is True
    assert "/app/cards/_bridge/download/" in detail["face_url"]


def test_같은_카드를_두_번_올려도_한_장이다(client) -> None:
    """앱이 재전송해도 도감에 두 장이 되면 안 됩니다 — 앱이 그것을 전제로
    id 를 만듭니다("재전송이 멱등해진다")."""
    card_id = uuid.uuid4()
    first = client.put(f"/app/cards/{card_id}", json=_body())
    second = client.put(f"/app/cards/{card_id}", json=_body())

    assert first.status_code == 201 and first.json()["created"] is True
    assert second.status_code == 200 and second.json()["created"] is False
    assert len(client.get("/app/cards").json()["cards"]) == 1


def test_이미_뽑은_카드는_다시_올려도_안_바뀐다(client) -> None:
    """**카드는 뽑힌 뒤로 안 바뀌는 물건입니다.** 여기서 값을 갈아끼우면 앱의 버그
    하나가 이미 뽑아 둔 카드를 조용히 바꿉니다."""
    card_id = uuid.uuid4()
    client.put(f"/app/cards/{card_id}", json=_body(template_id="cabbage", dog_name="네옹"))
    client.put(f"/app/cards/{card_id}", json=_body(template_id="pepper", dog_name="딴이름"))

    card = client.get(f"/app/cards/{card_id}").json()
    assert card["template_id"] == "cabbage"
    assert card["dog_name"] == "네옹"


def test_남이_가진_id_로_올리면_409(client, store) -> None:
    """⚠️ 앱이 id 를 만드는 구조라 **여기가 원칙 6 을 대신합니다.**

    404 가 아니라 409 인 이유는, 앱이 "id 를 다시 만들어 재시도" 할 수 있어야 하는데
    404 면 그걸 구분 못 하기 때문입니다.
    """
    alien_id = uuid.uuid4()
    store.dog_cards.append(
        DogCard(
            id=alien_id,
            app_user_id=STRANGER,
            template_id="cabbage",
            dog_name="남의카드",
            drawn_at=datetime.now(UTC),
            code_text="",
            user_framed=False,
            core_left=0, core_top=0, core_right=1, core_bottom=1,
        )
    )
    r = client.put(f"/app/cards/{alien_id}", json=_body())
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "card_belongs_to_someone_else"


def test_얼굴을_이미_올렸으면_티켓을_안_준다(client) -> None:
    """앱은 티켓이 없는 것을 보고 "이 카드는 다 됐다" 로 판단합니다."""
    card_id = uuid.uuid4()
    _sync(client, card_id)
    again = client.put(f"/app/cards/{card_id}", json=_body()).json()
    assert again["face_upload"] is None


# ── 카드가 들고 있는 것 ──────────────────────────────────────────────────


def test_인쇄된_이름은_아이와_따로_간다(client, store) -> None:
    """개명해도 **이미 뽑아 놓은 카드의 인쇄가 바뀌면 안 됩니다.**"""
    pet = FakePet(app_user_id=OWNER, name="네옹", breed="mix")
    store.pets.append(pet)
    card_id = uuid.uuid4()
    _sync(client, card_id, dog_id=str(pet.id), dog_name="네옹")

    pet.name = "새이름"  # 아이를 개명

    card = client.get(f"/app/cards/{card_id}").json()
    assert card["dog_name"] == "네옹"
    assert card["dog_id"] == str(pet.id)


def test_아이_없이도_뽑을_수_있다(client) -> None:
    card_id = uuid.uuid4()
    got = _sync(client, card_id, dog_id=None)
    assert got["card"]["dog_id"] is None


def test_남의_아이로_뽑았다고_적을_수_없다(client, store) -> None:
    other = FakePet(app_user_id=STRANGER, name="남의집", breed="mix")
    store.pets.append(other)
    r = client.put(f"/app/cards/{uuid.uuid4()}", json=_body(dog_id=str(other.id)))
    assert r.status_code == 404


def test_직접_맞춘_카드인지가_보존된다(client) -> None:
    """false 인 옛 카드는 예전 규칙으로 그립니다 — **업데이트로 달라지면 안 됩니다.**"""
    old_id, new_id = uuid.uuid4(), uuid.uuid4()
    _sync(client, old_id, user_framed=False)
    _sync(client, new_id, user_framed=True)

    rows = {c["id"]: c for c in client.get("/app/cards").json()["cards"]}
    assert rows[str(old_id)]["user_framed"] is False
    assert rows[str(new_id)]["user_framed"] is True


def test_얼굴_사각형이_뒤집히면_422(client) -> None:
    """**그리는 쪽에서 조용히 이상해집니다.** DB 까지 가면 500 이라 여기서 막습니다."""
    r = client.put(f"/app/cards/{uuid.uuid4()}", json=_body(core_right=5, core_left=99))
    assert r.status_code == 422


def test_목록은_최근에_뽑은_것부터다(client) -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    _sync(client, a, drawn_at="2026-09-01T00:00:00Z")
    _sync(client, b, drawn_at="2026-09-03T00:00:00Z")
    rows = client.get("/app/cards").json()["cards"]
    assert [c["id"] for c in rows] == [str(b), str(a)]


def test_목록은_얼굴_주소를_안_싣는다(client) -> None:
    """N 장마다 저장소를 두드리게 됩니다. 앱은 has_face 로 무엇을 받을지 압니다."""
    card_id = uuid.uuid4()
    _sync(client, card_id)
    row = client.get("/app/cards").json()["cards"][0]
    assert row["has_face"] is True
    assert row["face_url"] is None


# ── 지우기 · 탈퇴 ────────────────────────────────────────────────────────


def test_지우면_얼굴_그림까지_사라진다(client, storage) -> None:
    card_id = uuid.uuid4()
    got = _sync(client, card_id)
    key = got["face_upload"]["storage_key"]

    assert client.delete(f"/app/cards/{card_id}").status_code == 204
    assert not storage.local_path(key).exists()
    assert client.get("/app/cards").json()["cards"] == []


def test_남의_카드는_없는_것과_같다(client, store) -> None:
    alien_id = uuid.uuid4()
    store.dog_cards.append(
        DogCard(
            id=alien_id, app_user_id=STRANGER, template_id="cabbage", dog_name="남",
            drawn_at=datetime.now(UTC), code_text="", user_framed=False,
            core_left=0, core_top=0, core_right=1, core_bottom=1,
        )
    )
    assert client.get(f"/app/cards/{alien_id}").status_code == 404
    assert client.delete(f"/app/cards/{alien_id}").status_code == 404
    assert client.post(f"/app/cards/{alien_id}/face/confirm").status_code == 404
    assert client.get("/app/cards").json()["cards"] == []


def test_탈퇴하면_얼굴_그림이_사라진다(client, storage, store) -> None:
    """⚠️ `app_users` 행은 탈퇴해도 **남으므로** FK CASCADE 가 영영 안 돕니다."""
    a, b = uuid.uuid4(), uuid.uuid4()
    key_a = _sync(client, a, b"one")["face_upload"]["storage_key"]
    key_b = _sync(client, b, b"two")["face_upload"]["storage_key"]

    deleted = asyncio.run(card_service.cleanup_for_owner(None, OWNER))

    assert deleted == 2
    assert not storage.local_path(key_a).exists()
    assert not storage.local_path(key_b).exists()
    assert store.dog_cards == []


def test_카드가_없으면_저장소를_안_건드린다(monkeypatch, store) -> None:
    """저장소가 꺼졌다고 탈퇴가 막히면 안 됩니다."""
    monkeypatch.setattr(card_service, "get_storage", NotConfiguredStorage)
    assert asyncio.run(card_service.cleanup_for_owner(None, OWNER)) == 0


# ── bridge ───────────────────────────────────────────────────────────────


def test_발급된_적_없는_카드로는_못_올린다(client, storage) -> None:
    """이 검사가 없으면 **아무나 임의 경로로 서버 디스크를 채울 수 있습니다.**"""
    r = client.put(
        f"/app/cards/_bridge/upload/cards/{OWNER}/{uuid.uuid4()}/face.png",
        content=b"junk",
        headers={"Content-Type": PNG},
    )
    assert r.status_code == 404
    assert not list(storage.local_path("").rglob("*.png"))


def test_남의_카드_경로로는_못_올린다(client, store, storage) -> None:
    """키에 남의 user_id 를 적어 넣어도 그 카드가 그 사람 것인지를 봅니다."""
    alien_id = uuid.uuid4()
    store.dog_cards.append(
        DogCard(
            id=alien_id, app_user_id=STRANGER, template_id="cabbage", dog_name="남",
            drawn_at=datetime.now(UTC), code_text="", user_framed=False,
            core_left=0, core_top=0, core_right=1, core_bottom=1,
        )
    )
    r = client.put(
        f"/app/cards/_bridge/upload/cards/{OWNER}/{alien_id}/face.png",
        content=b"junk",
        headers={"Content-Type": PNG},
    )
    assert r.status_code == 404
    assert not list(storage.local_path("").rglob("*.png"))


def test_이상한_모양의_키는_404(client) -> None:
    for key in ("cards/x/face.png", "gait/a/b/face.png", "cards/notauuid/x/face.png"):
        r = client.put(
            f"/app/cards/_bridge/upload/{key}", content=b"x", headers={"Content-Type": PNG}
        )
        assert r.status_code == 404, key


def test_확정된_얼굴에는_덮어쓸_수_없다(client, storage) -> None:
    card_id = uuid.uuid4()
    got = _sync(client, card_id, b"original")
    key = got["face_upload"]["storage_key"]

    r = _upload(client, f"http://x/app/cards/_bridge/upload/{key}", b"swapped")
    assert r.status_code == 404
    assert storage.local_path(key).read_bytes() == b"original"


def test_PNG_가_아니면_거절한다(client) -> None:
    """구멍에 끼우려면 알파가 필요해서 JPEG 은 못 씁니다."""
    card_id = uuid.uuid4()
    got = client.put(f"/app/cards/{card_id}", json=_body()).json()
    r = _upload(client, got["face_upload"]["upload_url"], b"jpg", content_type="image/jpeg")
    assert r.status_code == 415


def test_상한을_넘으면_bridge_가_막고_흔적을_안_남긴다(client, storage, monkeypatch) -> None:
    monkeypatch.setattr(card_service, "MAX_CARD_FACE_BYTES", 4)
    card_id = uuid.uuid4()
    got = client.put(f"/app/cards/{card_id}", json=_body()).json()
    key = got["face_upload"]["storage_key"]

    r = _upload(client, got["face_upload"]["upload_url"], b"x" * 40)
    assert r.status_code == 413
    assert not storage.local_path(key).exists()


def test_빈_그림은_받지_않는다(client, storage) -> None:
    card_id = uuid.uuid4()
    got = client.put(f"/app/cards/{card_id}", json=_body()).json()
    r = _upload(client, got["face_upload"]["upload_url"], b"")
    assert r.status_code == 400
    assert not storage.local_path(got["face_upload"]["storage_key"]).exists()


def test_안_올리고_confirm_하면_거절한다(client) -> None:
    card_id = uuid.uuid4()
    client.put(f"/app/cards/{card_id}", json=_body())
    r = client.post(f"/app/cards/{card_id}/face/confirm")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "face_not_uploaded"


def test_confirm_을_두_번_해도_같다(client) -> None:
    card_id = uuid.uuid4()
    _sync(client, card_id)
    assert client.post(f"/app/cards/{card_id}/face/confirm").status_code == 200


def test_주소는_카드_bridge_를_가리킨다(client) -> None:
    """**보행 경로로 나가면 안 됩니다.**"""
    card_id = uuid.uuid4()
    _sync(client, card_id)
    url = client.get(f"/app/cards/{card_id}").json()["face_url"]
    assert "/app/cards/_bridge/download/" in url
    assert "/gait/" not in url


# ── 저장소가 꺼져 있을 때 ────────────────────────────────────────────────


def test_저장소가_꺼져_있으면_503_이고_사유는_안_샌다(client, monkeypatch) -> None:
    monkeypatch.setattr(card_service, "get_storage", NotConfiguredStorage)
    r = client.put(f"/app/cards/{uuid.uuid4()}", json=_body())
    assert r.status_code == 503
    assert r.json()["detail"] == "카드 보관은 아직 준비 중이에요."
    assert "GAIT_" not in r.text
