"""routers/screening.py + services/screening.py — **피부 변화 기록** (`/app/screening/*`).

리포지토리는 `fakes.py` 가, 저장소는 임시 디렉터리 위의 **진짜** `LocalBridgeStorage`
가 맡습니다 (프로필 사진 테스트와 같은 이유 — create-only·스트리밍·부분 파일 정리가
구현에 붙어 있는 성질이라 가짜로 바꾸면 정작 보고 싶은 것이 안 보입니다).

**모델은 가짜입니다.** 진짜 가중치는 350MB 이고 사진 한 장에 CPU 0.6~3초라 테스트가
못 씁니다. `_run_model` 을 통째로 바꿔치기해서, 여기서 보는 것은 **판정 결과가 아니라
기록의 규칙**입니다 — 남의 기록을 볼 수 있는가, 판정 뒤에 사진이 덮이는가, 탈퇴하면
사진이 사라지는가.
"""

import asyncio
import uuid

import pytest
from fakes import FakeAdmin, FakeAppUser, FakePet, Store, install
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.core import storage as storage_module
from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
from daengs_backend.core.storage import LocalBridgeStorage, NotConfiguredStorage
from daengs_backend.models import ScreeningRecord
from daengs_backend.routers import screening as screening_router
from daengs_backend.services import screening as screening_service

OWNER = uuid.uuid4()
STRANGER = uuid.uuid4()

JPEG = "image/jpeg"
FAKE_RESULT = {"stage1": {"label": "정상"}, "score": 0.1}


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    s = install(Store(FakeAdmin()), monkeypatch)
    s.add_app_user(FakeAppUser(kakao_id=1, id=OWNER))
    return s


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch, tmp_path) -> LocalBridgeStorage:
    s = LocalBridgeStorage(str(tmp_path), base_url="http://x")
    monkeypatch.setattr(storage_module, "get_storage", lambda: s)
    monkeypatch.setattr(screening_service, "get_storage", lambda: s)
    return s


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch):
    """모델을 가짜로. 진짜는 가중치 350MB + CPU 몇 초라 테스트가 못 씁니다."""
    calls: list[tuple[bytes, object]] = []

    def fake(image_bytes, box):
        calls.append((image_bytes, box))
        return FAKE_RESULT, "v9-test"

    monkeypatch.setattr(screening_service, "_run_model", fake)
    return calls


@pytest.fixture
def client(store: Store, storage: LocalBridgeStorage, model) -> TestClient:
    app = FastAPI()
    app.include_router(screening_router.router)
    app.dependency_overrides[
        next(iter(CurrentAppUser.__metadata__)).dependency
    ] = lambda: AppPrincipal(app_user_id=OWNER)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def pet(store: Store) -> FakePet:
    p = FakePet(app_user_id=OWNER, name="네옹", breed="toy_poodle_light_brown")
    store.pets.append(p)
    return p


def _upload(client: TestClient, url: str, data: bytes, content_type: str = JPEG):
    return client.put(
        url.removeprefix("http://x"),
        content=data,
        headers={"Content-Type": content_type},
    )


def _round_trip(client: TestClient, data: bytes = b"skin-photo", **body) -> dict:
    """티켓 → PUT → confirm 한 바퀴. confirm 응답을 돌려줍니다."""
    ticket = client.post("/app/screening/records", json=body).json()
    assert _upload(client, ticket["upload_url"], data).status_code == 200
    r = client.post(f"/app/screening/records/{ticket['record_id']}/confirm")
    assert r.status_code == 200, r.text
    return {**r.json(), "storage_key": ticket["storage_key"]}


# ── 한 바퀴 ──────────────────────────────────────────────────────────────


def test_티켓부터_판정까지_한_바퀴(client, pet, storage, model) -> None:
    got = _round_trip(client, pet_id=str(pet.id))

    assert got["status"] == "DONE"
    assert got["result"] == FAKE_RESULT
    assert got["contract_version"] == "v9-test"
    assert got["pet_id"] == str(pet.id)
    # **사진은 판정 뒤에도 남습니다** — 사진 자체가 기록입니다.
    assert storage.local_path(got["storage_key"]).read_bytes() == b"skin-photo"


def test_아이를_안_골라도_기록할_수_있다(client) -> None:
    """아이를 아직 등록 안 했을 수 있습니다. 그때 못 찍게 하면 기능이 죽습니다."""
    got = _round_trip(client)
    assert got["pet_id"] is None
    assert got["status"] == "DONE"


def test_가이드_프레임이_모델까지_간다(client, model) -> None:
    """안 넘기면 화면 중앙으로 물러서는데, **2단계 분포가 학습 크롭과 어긋납니다.**"""
    box = [0.1, 0.2, 0.5, 0.5]
    _round_trip(client, box=box)
    assert model[-1][1] == box


def test_기록은_최근_순이다(client, pet) -> None:
    first = _round_trip(client, b"first", pet_id=str(pet.id))
    second = _round_trip(client, b"second", pet_id=str(pet.id))

    rows = client.get("/app/screening/records").json()["records"]
    assert [r["record_id"] for r in rows] == [second["record_id"], first["record_id"]]


def test_아이로_거를_수_있다(client, pet, store) -> None:
    mine = FakePet(app_user_id=OWNER, name="두찌", breed="mix")
    store.pets.append(mine)
    a = _round_trip(client, pet_id=str(pet.id))
    _round_trip(client, pet_id=str(mine.id))

    rows = client.get(f"/app/screening/records?pet_id={pet.id}").json()["records"]
    assert [r["record_id"] for r in rows] == [a["record_id"]]


def test_목록은_사진_주소를_안_싣는다(client) -> None:
    """N 개마다 저장소를 두드리게 되고, 앱이 안 그리는 것까지 만듭니다."""
    _round_trip(client)
    row = client.get("/app/screening/records").json()["records"][0]
    assert row["photo_url"] is None

    detail = client.get(f"/app/screening/records/{row['record_id']}").json()
    assert "/app/screening/_bridge/download/" in detail["photo_url"]


# ── confirm 이 하는 일 ───────────────────────────────────────────────────


def test_안_올리고_confirm_하면_거절한다(client) -> None:
    ticket = client.post("/app/screening/records", json={}).json()
    r = client.post(f"/app/screening/records/{ticket['record_id']}/confirm")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "photo_not_uploaded"


def test_confirm_을_두_번_해도_같은_답이다(client) -> None:
    """다시 돌리면 같은 사진에 다른 답이 남을 수 있습니다."""
    got = _round_trip(client)
    again = client.post(f"/app/screening/records/{got['record_id']}/confirm")
    assert again.status_code == 200
    assert again.json()["result"] == got["result"]


def test_판정이_터져도_기록은_FAILED_로_남는다(client, monkeypatch, storage) -> None:
    """**행을 지우면 사진이 고아가 됩니다** — 이미 저장소에 올라가 있습니다."""
    def boom(image_bytes, box):
        raise RuntimeError("모델 안에서 터짐")

    monkeypatch.setattr(screening_service, "_run_model", boom)

    ticket = client.post("/app/screening/records", json={}).json()
    _upload(client, ticket["upload_url"], b"photo")
    r = client.post(f"/app/screening/records/{ticket['record_id']}/confirm")

    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "screening_failed"

    row = client.get(f"/app/screening/records/{ticket['record_id']}").json()
    assert row["status"] == "FAILED"
    # 사진은 남아 있어야 합니다 — 다시 찍을지 정하려면 봐야 합니다.
    assert storage.local_path(ticket["storage_key"]).exists()
    assert row["photo_url"] is not None


def test_가중치가_없으면_503_이고_FAILED_로_안_남긴다(client, monkeypatch) -> None:
    """서버에 가중치가 없는 것이라 **사용자 잘못이 아닙니다.** 가중치를 놓으면
    같은 사진으로 다시 confirm 할 수 있어야 합니다."""
    def missing(image_bytes, box):
        raise screening_service.ScreeningModelUnavailableError("가중치 없음")

    monkeypatch.setattr(screening_service, "_run_model", missing)

    ticket = client.post("/app/screening/records", json={}).json()
    _upload(client, ticket["upload_url"], b"photo")
    r = client.post(f"/app/screening/records/{ticket['record_id']}/confirm")

    assert r.status_code == 503
    # 사유가 새면 안 됩니다 — 경로가 들어 있습니다.
    assert "SCREENING_RELEASE_DIR" not in r.text
    assert client.get(f"/app/screening/records/{ticket['record_id']}").json()[
        "status"
    ] == "PENDING_UPLOAD"


def test_상한을_넘으면_confirm_도_거절한다(client, monkeypatch) -> None:
    monkeypatch.setattr(screening_service, "MAX_SCREENING_PHOTO_BYTES", 4)
    ticket = client.post("/app/screening/records", json={}).json()
    storage_module.get_storage().write(ticket["storage_key"], b"toolong")
    r = client.post(f"/app/screening/records/{ticket['record_id']}/confirm")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "invalid_photo_size"


# ── 남의 것 ──────────────────────────────────────────────────────────────


def test_남의_아이로는_기록을_못_연다(client, store) -> None:
    other = FakePet(app_user_id=STRANGER, name="남의집", breed="mix")
    store.pets.append(other)
    r = client.post("/app/screening/records", json={"pet_id": str(other.id)})
    assert r.status_code == 404


# ── 공동 돌봄 — 생성은 아직 대표만 (docs/co-care.md §2, Task 12 fix round 1) ─────


def _client_as(app_user_id: uuid.UUID) -> TestClient:
    app = FastAPI()
    app.include_router(screening_router.router)
    app.dependency_overrides[
        next(iter(CurrentAppUser.__metadata__)).dependency
    ] = lambda: AppPrincipal(app_user_id=app_user_id)
    return TestClient(app, raise_server_exceptions=False)


def test_돌보미는_아직_기록을_못_연다(store, pet) -> None:
    """⚠️ **의도적으로 대표만입니다.** `gait_records` 는 `pet_id → pets.app_user_id`
    로 소유가 유도되어 생성을 구성원(대표 ∪ 돌보미)으로 열어도 대표가 그대로
    봅니다(`repositories/gait_record.py`). `screening_records` 는 다릅니다 — 소유가
    만든 사람(`ScreeningRecord.app_user_id`)에 **직접** 저장되고
    `repositories/screening.py` 는 `pet_repo.member_condition` 을 쓴 적이 없습니다.

    돌보미의 생성만 열면 **대표가 못 보는** 스크리닝 기록이 생깁니다 — 한 집의 피부
    이력이 둘로 쪼개지는데 어느 쪽도 전체를 못 봅니다. 그래서 여기는 닫아 둡니다
    (Task 12 fix round 1). `pet_repo.get_accessible` 로 바꾸면 이 테스트가 201 을
    받아 실패합니다 — 그것이 이 테스트가 가르는 것입니다.
    """
    carer = uuid.uuid4()
    store.pet_members.append((pet.id, carer))

    r = _client_as(carer).post("/app/screening/records", json={"pet_id": str(pet.id)})
    assert r.status_code == 404


def test_남의_기록은_없는_것과_같다(client, store) -> None:
    """403 을 주면 "그 기록이 존재한다" 가 샙니다."""
    alien = ScreeningRecord(
        id=uuid.uuid4(),
        app_user_id=STRANGER,
        status="DONE",
        photo_storage_key=f"screening/{STRANGER}/x/photo.jpg",
        photo_content_type=JPEG,
    )
    store.screenings.append(alien)

    assert client.get(f"/app/screening/records/{alien.id}").status_code == 404
    assert client.post(f"/app/screening/records/{alien.id}/confirm").status_code == 404
    assert client.delete(f"/app/screening/records/{alien.id}").status_code == 404
    assert client.get("/app/screening/records").json()["records"] == []


# ── 지우기 · 탈퇴 ────────────────────────────────────────────────────────


def test_지우면_사진까지_사라진다(client, storage) -> None:
    got = _round_trip(client)
    assert client.delete(f"/app/screening/records/{got['record_id']}").status_code == 204
    assert not storage.local_path(got["storage_key"]).exists()
    assert client.get("/app/screening/records").json()["records"] == []


def test_탈퇴하면_사진이_사라진다(client, storage, store) -> None:
    """⚠️ `app_users` 행은 탈퇴해도 **남으므로** FK CASCADE 가 영영 안 돕니다.
    여기를 빠뜨리면 공개한 처리방침 4항을 못 지킵니다 — 점령지 사진이 지금 그 상태입니다."""
    a = _round_trip(client, b"one")
    b = _round_trip(client, b"two")

    deleted = asyncio.run(screening_service.cleanup_for_owner(None, OWNER))

    assert deleted == 2
    assert not storage.local_path(a["storage_key"]).exists()
    assert not storage.local_path(b["storage_key"]).exists()
    assert store.screenings == []


def test_기록이_없으면_저장소를_안_건드린다(monkeypatch, store) -> None:
    """저장소가 꺼졌다고 탈퇴가 막히면 안 됩니다."""
    monkeypatch.setattr(screening_service, "get_storage", NotConfiguredStorage)
    assert asyncio.run(screening_service.cleanup_for_owner(None, OWNER)) == 0


# ── bridge ───────────────────────────────────────────────────────────────


def test_발급된_적_없는_키로는_못_올린다(client, storage) -> None:
    """이 검사가 없으면 **아무나 임의 경로로 서버 디스크를 채울 수 있습니다.**"""
    r = client.put(
        "/app/screening/_bridge/upload/screening/x/y/photo.jpg",
        content=b"junk",
        headers={"Content-Type": JPEG},
    )
    assert r.status_code == 404
    assert not list(storage.local_path("").rglob("*.jpg"))


def test_판정이_끝난_기록에는_못_덮어쓴다(client, storage) -> None:
    """덮어쓸 수 있으면 화면의 판정과 사진이 어긋납니다."""
    got = _round_trip(client, b"original")
    r = client.put(
        f"/app/screening/_bridge/upload/{got['storage_key']}",
        content=b"swapped",
        headers={"Content-Type": JPEG},
    )
    assert r.status_code == 404
    assert storage.local_path(got["storage_key"]).read_bytes() == b"original"


def test_같은_티켓으로_두_번_못_올린다(client) -> None:
    ticket = client.post("/app/screening/records", json={}).json()
    assert _upload(client, ticket["upload_url"], b"first").status_code == 200
    assert _upload(client, ticket["upload_url"], b"second").status_code == 409


def test_발급된_형식과_다른_Content_Type_은_거절한다(client) -> None:
    ticket = client.post(
        "/app/screening/records", json={"content_type": JPEG}
    ).json()
    r = _upload(client, ticket["upload_url"], b"webp", content_type="image/webp")
    assert r.status_code == 415


def test_상한을_넘으면_bridge_가_막고_흔적을_안_남긴다(client, storage, monkeypatch) -> None:
    monkeypatch.setattr(screening_service, "MAX_SCREENING_PHOTO_BYTES", 4)
    ticket = client.post("/app/screening/records", json={}).json()
    r = _upload(client, ticket["upload_url"], b"x" * 40)
    assert r.status_code == 413
    assert not storage.local_path(ticket["storage_key"]).exists()


def test_빈_사진은_받지_않는다(client, storage) -> None:
    ticket = client.post("/app/screening/records", json={}).json()
    r = _upload(client, ticket["upload_url"], b"")
    assert r.status_code == 400
    assert not storage.local_path(ticket["storage_key"]).exists()


def test_실패한_기록의_사진도_내려받을_수_있다(client, monkeypatch, storage) -> None:
    """판정이 안 됐을 뿐 사용자가 찍은 사진입니다. 다시 찍을지 정하려면 봐야 합니다."""
    def boom(image_bytes, box):
        raise RuntimeError("터짐")

    monkeypatch.setattr(screening_service, "_run_model", boom)
    ticket = client.post("/app/screening/records", json={}).json()
    _upload(client, ticket["upload_url"], b"photo")
    client.post(f"/app/screening/records/{ticket['record_id']}/confirm")

    r = client.get(f"/app/screening/_bridge/download/{ticket['storage_key']}")
    assert r.status_code == 200
    assert r.content == b"photo"


def test_주소는_피부_bridge_를_가리킨다(client) -> None:
    """**보행 경로로 나가면 안 됩니다.**"""
    got = _round_trip(client)
    url = client.get(f"/app/screening/records/{got['record_id']}").json()["photo_url"]
    assert "/app/screening/_bridge/download/" in url
    assert "/gait/" not in url


# ── 입력 검증 ────────────────────────────────────────────────────────────


def test_픽셀_좌표를_보내면_422(client) -> None:
    """정규화가 아니면 크롭이 사진 바깥을 가리키고, 판정은 **에러가 아니라
    그냥 이상한 답**을 냅니다 — 그래서 여기서 잡아야 합니다."""
    r = client.post("/app/screening/records", json={"box": [10, 20, 300, 300]})
    assert r.status_code == 422


def test_너비가_0인_프레임은_422(client) -> None:
    r = client.post("/app/screening/records", json={"box": [0.1, 0.1, 0.0, 0.5]})
    assert r.status_code == 422


# ── 저장소가 꺼져 있을 때 ────────────────────────────────────────────────


def test_저장소가_꺼져_있으면_503_이고_사유는_안_샌다(client, monkeypatch) -> None:
    monkeypatch.setattr(screening_service, "get_storage", NotConfiguredStorage)
    r = client.post("/app/screening/records", json={})
    assert r.status_code == 503
    assert r.json()["detail"] == "피부 기록은 아직 준비 중이에요."
    assert "GAIT_" not in r.text
