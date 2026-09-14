"""routers/pet.py + services/pet.py — **프로필 사진** (D-052).

DB 도 진짜 저장소도 안 씁니다... 정확히는 **저장소는 진짜를 씁니다.** 리포지토리는
`fakes.py` 가 바꿔치기하지만, 저장소는 임시 디렉터리 위의 진짜 `LocalBridgeStorage`
입니다 — create-only·스트리밍·부분 파일 정리가 **구현에 붙어 있는 성질**이라
가짜로 바꾸면 정작 보고 싶은 것이 안 보입니다.

여기서 보는 것은 규칙입니다 — 남의 아이에 사진을 걸 수 있는가, confirm 없이 화면에
보이는가, 사진을 바꾸면 옛 파일이 남는가, 지웠는데 파일이 남는가.
"""

import asyncio
import uuid

import pytest
from fakes import FakeAppUser, FakePet, Store
from fastapi.testclient import TestClient

from daengs_backend.core import storage as storage_module
from daengs_backend.core.storage import LocalBridgeStorage, NotConfiguredStorage
from daengs_backend.services import pet as pet_service
from tests.pets.support.api import make_client, make_store

OWNER = uuid.uuid4()
STRANGER = uuid.uuid4()

JPEG = "image/jpeg"


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    return make_store(OWNER, monkeypatch)


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch, tmp_path) -> LocalBridgeStorage:
    """진짜 LocalBridgeStorage 를 임시 디렉터리 위에 세웁니다."""
    s = LocalBridgeStorage(str(tmp_path), base_url="http://x")
    monkeypatch.setattr(storage_module, "get_storage", lambda: s)
    monkeypatch.setattr(pet_service, "get_storage", lambda: s)
    return s


@pytest.fixture
def client(store: Store, storage: LocalBridgeStorage) -> TestClient:
    return make_client(OWNER)


@pytest.fixture
def pet(store: Store) -> FakePet:
    p = FakePet(app_user_id=OWNER, name="네옹", breed="toy_poodle_light_brown")
    store.pets.append(p)
    return p


def _upload(client: TestClient, url: str, data: bytes, content_type: str = JPEG):
    """티켓의 upload_url 로 앱이 하는 것과 같은 PUT."""
    return client.put(
        url.removeprefix("http://x"),
        content=data,
        headers={"Content-Type": content_type},
    )


def _round_trip(client: TestClient, pet_id: uuid.UUID, data: bytes = b"jpeg-bytes") -> str:
    """티켓 → PUT → confirm 한 바퀴. 확정된 키를 돌려줍니다."""
    ticket = client.post(f"/app/pets/{pet_id}/photo").json()
    assert _upload(client, ticket["upload_url"], data).status_code == 200
    assert client.post(f"/app/pets/{pet_id}/photo/confirm").status_code == 200
    return ticket["storage_key"]


# ── 한 바퀴 ──────────────────────────────────────────────────────────────


def test_티켓부터_confirm까지_한_바퀴(client, pet, storage) -> None:
    key = _round_trip(client, pet.id)

    assert storage.local_path(key).read_bytes() == b"jpeg-bytes"
    assert pet.photo_storage_key == key
    assert pet.photo_size_bytes == len(b"jpeg-bytes")
    # 대기 칸은 비워져야 합니다 — 안 비우면 다음 업로드가 옛 티켓을 봅니다.
    assert pet.photo_pending_key is None


def test_키는_추측할_수_없어야_한다(client, pet) -> None:
    """**bridge 는 키를 아는 것이 자격입니다.** `pets/<pet_id>/profile.jpg` 처럼
    뻔한 키면 pet_id 만 알면 남의 사진을 받을 수 있습니다."""
    key = client.post(f"/app/pets/{pet.id}/photo").json()["storage_key"]
    assert key.startswith(f"pets/{pet.id}/profile/")
    # uuid4 hex 32자 + 확장자.
    assert len(key.rsplit("/", 1)[1]) == len(uuid.uuid4().hex) + len(".jpg")


def test_목록은_주소를_안_싣고_있다는_것만_알려_준다(client, pet) -> None:
    """주소를 목록에 담으면 N 마리마다 저장소를 두드립니다."""
    assert client.get("/app/pets").json()["pets"][0]["has_photo"] is False
    _round_trip(client, pet.id)
    row = client.get("/app/pets").json()["pets"][0]
    assert row["has_photo"] is True
    assert row["photo_updated_at"] is not None
    assert "download_url" not in row


# ── confirm 이 하는 일 ───────────────────────────────────────────────────


def test_confirm_전에는_화면에_안_보인다(client, pet) -> None:
    """올리기만 하고 confirm 을 안 하면 아직 사진이 아닙니다 — 크기·형식을 아직
    아무도 안 봤습니다."""
    ticket = client.post(f"/app/pets/{pet.id}/photo").json()
    _upload(client, ticket["upload_url"], b"jpeg")

    assert client.get("/app/pets").json()["pets"][0]["has_photo"] is False
    assert client.get(f"/app/pets/{pet.id}/photo").status_code == 409


def test_안_올리고_confirm_하면_거절한다(client, pet) -> None:
    """앱이 "올렸어요" 라고 말하는 것만 믿으면 없는 사진을 가리키는 행이 생깁니다."""
    client.post(f"/app/pets/{pet.id}/photo")
    r = client.post(f"/app/pets/{pet.id}/photo/confirm")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "photo_not_uploaded"


def test_티켓_없이_confirm_하면_거절한다(client, pet) -> None:
    r = client.post(f"/app/pets/{pet.id}/photo/confirm")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_pending_photo"


def test_상한을_넘으면_confirm_도_거절한다(client, pet, monkeypatch) -> None:
    """bridge 가 먼저 막지만, 저장소에 직접 올라가는 경우(GCS)도 있어서 여기서도 봅니다."""
    monkeypatch.setattr(pet_service, "MAX_PET_PHOTO_BYTES", 4)
    ticket = client.post(f"/app/pets/{pet.id}/photo").json()
    # bridge 를 거치지 않고 저장소에 바로 씁니다 — GCS 직접 업로드와 같은 상황.
    storage_module.get_storage().write(ticket["storage_key"], b"toolong")
    r = client.post(f"/app/pets/{pet.id}/photo/confirm")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "invalid_photo_size"


# ── 바꾸기 · 지우기 ──────────────────────────────────────────────────────


def test_사진을_바꾸면_옛_파일이_사라진다(client, pet, storage) -> None:
    """**같은 키에 덮어쓰지 않습니다** — 덮어쓰면 앱이 옛 사진을 계속 그립니다.
    새 키로 올리는 대신 옛 객체는 여기서 지워야 볼륨에 안 쌓입니다."""
    first = _round_trip(client, pet.id, b"first")
    second = _round_trip(client, pet.id, b"second")

    assert first != second
    assert not storage.local_path(first).exists()
    assert storage.local_path(second).read_bytes() == b"second"


def test_티켓을_다시_받으면_올리다_만_것이_사라진다(client, pet, storage) -> None:
    """사진을 골랐다가 다시 고르는 것은 자연스러운 일이라 막지 않습니다. 다만
    버린 것을 안 지우면 아무도 안 보는 파일이 쌓입니다."""
    first = client.post(f"/app/pets/{pet.id}/photo").json()
    _upload(client, first["upload_url"], b"discarded")

    client.post(f"/app/pets/{pet.id}/photo")

    assert not storage.local_path(first["storage_key"]).exists()


def test_지우면_파일까지_사라진다(client, pet, storage) -> None:
    key = _round_trip(client, pet.id)
    assert client.delete(f"/app/pets/{pet.id}/photo").status_code == 204

    assert not storage.local_path(key).exists()
    assert pet.photo_storage_key is None
    assert client.get("/app/pets").json()["pets"][0]["has_photo"] is False


def test_사진이_없어도_지우기는_성공한다(client, pet) -> None:
    """두 번 눌러도 같은 결과여야 합니다."""
    assert client.delete(f"/app/pets/{pet.id}/photo").status_code == 204
    assert client.delete(f"/app/pets/{pet.id}/photo").status_code == 204


def test_아이를_지우면_사진도_같이_지워진다(client, pet, storage) -> None:
    """**행이 먼저 사라지면 키를 잃어 객체가 영구 고아입니다** — 저장소에는 FK 가
    없습니다. 점령지 사진이 탈퇴 때 안 지워지던 것과 같은 실수를 안 하려는 자리입니다."""
    key = _round_trip(client, pet.id)
    assert client.delete(f"/app/pets/{pet.id}").status_code == 204
    assert not storage.local_path(key).exists()


def test_탈퇴하면_사진이_사라진다(client, pet, storage) -> None:
    """공개한 처리방침 4항("탈퇴 시 지체 없이 파기")을 지키려면 DB 행만으로 모자랍니다."""
    key = _round_trip(client, pet.id)

    asyncio.run(pet_service.delete_all_for_owner(None, OWNER))

    assert not storage.local_path(key).exists()


# ── 남의 것 ──────────────────────────────────────────────────────────────


def test_남의_아이에는_사진을_못_건다(client, store) -> None:
    """**없는 것과 남의 것은 같은 404 입니다** — 403 이면 "그 id 는 존재한다"가 샙니다."""
    other = FakePet(app_user_id=STRANGER, name="남의집", breed="mix")
    store.pets.append(other)

    assert client.post(f"/app/pets/{other.id}/photo").status_code == 404
    assert client.post(f"/app/pets/{other.id}/photo/confirm").status_code == 404
    assert client.get(f"/app/pets/{other.id}/photo").status_code == 404
    assert client.delete(f"/app/pets/{other.id}/photo").status_code == 404


# ── bridge ───────────────────────────────────────────────────────────────


def test_발급된_적_없는_키로는_못_올린다(client, pet, storage) -> None:
    """bridge 는 인증 헤더가 없는 자리라, 이 검사가 없으면 **아무나 임의 경로로
    서버 디스크를 채울 수 있습니다.**"""
    r = client.put(
        "/app/pets/_bridge/upload/pets/x/profile/attacker.jpg",
        content=b"junk",
        headers={"Content-Type": JPEG},
    )
    assert r.status_code == 404
    assert not list(storage.local_path("").rglob("*.jpg"))


def test_확정된_키에는_덮어쓸_수_없다(client, pet, storage) -> None:
    """confirm 뒤에는 대기 키가 비므로 그 키로 올라오는 PUT 은 없는 경로와 같습니다."""
    key = _round_trip(client, pet.id, b"original")
    r = client.put(
        f"/app/pets/_bridge/upload/{key}",
        content=b"overwritten",
        headers={"Content-Type": JPEG},
    )
    assert r.status_code == 404
    assert storage.local_path(key).read_bytes() == b"original"


def test_같은_티켓으로_두_번_못_올린다(client, pet) -> None:
    """create-only. 키가 새도 살아 있는 객체를 못 덮게 하는 자리입니다."""
    ticket = client.post(f"/app/pets/{pet.id}/photo").json()
    assert _upload(client, ticket["upload_url"], b"first").status_code == 200
    assert _upload(client, ticket["upload_url"], b"second").status_code == 409


def test_발급된_형식과_다른_Content_Type_은_거절한다(client, pet) -> None:
    ticket = client.post(f"/app/pets/{pet.id}/photo", json={"content_type": JPEG}).json()
    r = _upload(client, ticket["upload_url"], b"webp", content_type="image/webp")
    assert r.status_code == 415


def test_상한을_넘으면_bridge_가_막고_흔적을_안_남긴다(client, pet, storage, monkeypatch) -> None:
    """**중간에 끊긴 파일이 남으면 안 됩니다** — 다음 PUT 이 create-only 409 에 막히고,
    0바이트로 남으면 redact() 의 tombstone 과 구별되지 않습니다."""
    monkeypatch.setattr(pet_service, "MAX_PET_PHOTO_BYTES", 4)
    ticket = client.post(f"/app/pets/{pet.id}/photo").json()

    r = _upload(client, ticket["upload_url"], b"x" * 40)
    assert r.status_code == 413
    assert not storage.local_path(ticket["storage_key"]).exists()


def test_빈_사진은_받지_않는다(client, pet, storage) -> None:
    ticket = client.post(f"/app/pets/{pet.id}/photo").json()
    r = _upload(client, ticket["upload_url"], b"")
    assert r.status_code == 400
    assert not storage.local_path(ticket["storage_key"]).exists()


def test_bridge_다운로드는_확정된_것만_내려준다(client, pet, storage) -> None:
    """대기 키는 크기·형식을 아직 아무도 안 본 바이트입니다. 그걸 그리면
    confirm 이 하는 일이 무의미해집니다."""
    ticket = client.post(f"/app/pets/{pet.id}/photo").json()
    _upload(client, ticket["upload_url"], b"not-yet")
    key = ticket["storage_key"]
    assert client.get(f"/app/pets/_bridge/download/{key}").status_code == 404

    client.post(f"/app/pets/{pet.id}/photo/confirm")
    r = client.get(f"/app/pets/_bridge/download/{key}")
    assert r.status_code == 200
    assert r.content == b"not-yet"


def test_주소는_사진_bridge_를_가리킨다(client, pet) -> None:
    """**보행 경로로 나가면 안 됩니다.** `download_url` 이 gait 를 하드코딩하고 있어서
    프로필 사진이 보행 라우터에 걸려 404 가 나던 자리입니다."""
    _round_trip(client, pet.id)
    url = client.get(f"/app/pets/{pet.id}/photo").json()["download_url"]
    assert "/app/pets/_bridge/download/" in url
    assert "/gait/" not in url


# ── 저장소가 꺼져 있을 때 ────────────────────────────────────────────────


def test_저장소가_꺼져_있으면_503_이고_사유는_안_샌다(client, pet, monkeypatch) -> None:
    """`StorageNotConfiguredError` 는 운영자용이라 환경 변수 이름이 들어 있습니다.
    보행 라우터가 그것을 앱 화면에 그대로 띄운 적이 있습니다 (2026-09-03)."""
    monkeypatch.setattr(pet_service, "get_storage", NotConfiguredStorage)

    r = client.post(f"/app/pets/{pet.id}/photo")
    assert r.status_code == 503
    assert r.json()["detail"] == "사진 기능은 아직 준비 중이에요."
    assert "GAIT_" not in r.text


def test_저장소가_꺼져_있어도_사진_없는_아이는_지울_수_있다(client, pet, monkeypatch) -> None:
    """지울 사진이 없으면 저장소를 안 건드립니다 — 저장소가 꺼졌다고 탈퇴가
    막히면 안 됩니다."""
    monkeypatch.setattr(pet_service, "get_storage", NotConfiguredStorage)
    assert client.delete(f"/app/pets/{pet.id}").status_code == 204


# ── 연결된 그룹에서의 사진 (공동 돌봄) ──────────────────────────────────────
#
# **사진은 보호자마다 자기 값입니다.** 이름과 같은 규칙이라 그룹 주보호자가 아니어도
# 자기 행의 사진을 걸 수 있어야 합니다 — 공통 정보를 바꾸는 전체 PUT·삭제와 다릅니다.
# 그 성질이 `get_owned` 하나에만 기대고 있어서, 연결된 상태에서 실제로 그런지를 봅니다.


@pytest.fixture
def linked_pair(store: Store):
    """A(그룹 주보호자)의 행과 B(연결한 공동 보호자)의 행. B 는 A 행의 돌보미이기도 합니다."""
    from fakes import FakeIdentity

    b_user = uuid.uuid4()
    store.add_app_user(FakeAppUser(kakao_id=2, id=b_user))
    a_pet = FakePet(app_user_id=OWNER, name="롱이씨", breed="dog_pug")
    b_pet = FakePet(app_user_id=b_user, name="롱롱씨", breed="dog_beagle")
    identity = FakeIdentity(owner_pet_id=a_pet.id)
    a_pet.identity_id = identity.id
    b_pet.identity_id = identity.id
    store.pets += [a_pet, b_pet]
    store.pet_identities.append(identity)
    store.pet_members.append((a_pet.id, b_user))
    return a_pet, b_pet, b_user


def test_연결된_공동보호자도_자기_행_사진을_건다(store, storage, linked_pair) -> None:
    """티켓 → PUT → confirm 한 바퀴가 그룹 주보호자가 아니어도 돌아야 합니다."""
    _a_pet, b_pet, b_user = linked_pair
    as_b = make_client(b_user)

    key = _round_trip(as_b, b_pet.id)

    assert storage.local_path(key).read_bytes() == b"jpeg-bytes"
    assert b_pet.photo_storage_key == key


def test_연결된_공동보호자가_자기_행_사진을_지운다(store, storage, linked_pair) -> None:
    _a_pet, b_pet, b_user = linked_pair
    as_b = make_client(b_user)
    _round_trip(as_b, b_pet.id)

    assert as_b.delete(f"/app/pets/{b_pet.id}/photo").status_code == 204
    assert b_pet.photo_storage_key is None


def test_사진은_그룹_주보호자의_행을_안_건드린다(store, storage, linked_pair) -> None:
    """**공통 정보가 아닙니다.** B 가 자기 사진을 걸어도 A 의 사진은 그대로여야 합니다."""
    a_pet, b_pet, b_user = linked_pair
    a_pet.photo_storage_key = "pets/a/profile/fixed.jpg"

    _round_trip(make_client(b_user), b_pet.id)

    assert a_pet.photo_storage_key == "pets/a/profile/fixed.jpg"
    assert b_pet.photo_storage_key != a_pet.photo_storage_key


def test_연결돼도_남의_행_사진은_못_건다(store, storage, linked_pair) -> None:
    """B 는 A 행의 **돌보미**지만 사진은 행 소유자만 겁니다 — 읽기 권한과 다릅니다."""
    a_pet, _b_pet, b_user = linked_pair
    as_b = make_client(b_user)

    assert as_b.post(f"/app/pets/{a_pet.id}/photo").status_code == 404
    assert as_b.post(f"/app/pets/{a_pet.id}/photo/confirm").status_code == 404
    assert as_b.delete(f"/app/pets/{a_pet.id}/photo").status_code == 404
