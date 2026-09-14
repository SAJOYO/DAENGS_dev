"""`services/ai_card.py` — 앱 사용자 AI 카드의 시작·백그라운드 생성·조회·삭제 (#537, D-076).

리포지토리는 `fakes.py`, 저장소는 임시 디렉터리 위의 **진짜** `LocalBridgeStorage`, 엔진·검수는
`cardimage_fakes.py`. 백그라운드 작업은 `_spawn` 을 가로채 모아 두었다가 테스트가 직접 돌린다 —
그래야 "요청은 끝났고 생성은 아직" 인 사이의 상태를 볼 수 있다.
"""

import asyncio
import io
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from cardimage_fakes import FakeEngine, FakeJudge
from fakes import FakeAdmin, FakeAppUser, FakePet, FakeSession, Store, install
from PIL import Image
from pydantic import SecretStr

from daengs_backend.config import settings
from daengs_backend.core import storage as storage_module
from daengs_backend.core.storage import LocalBridgeStorage, NotConfiguredStorage, StorageNotConfiguredError
from daengs_backend.models import AiCard
from daengs_backend.services import ai_card as service
from daengs_backend.services import ai_card_engine
from daengs_backend.services import ai_card_quota as quota
from daengs_cardimage import CardImageUnavailable
from daengs_cardimage.catalog import MonthNotOpenError
from daengs_cardimage.engine import EngineError
from daengs_cardimage.photo import PhotoError

OWNER = uuid.uuid4()
STRANGER = uuid.uuid4()


class _SessionFactory:
    """`SessionLocal()` 대역 — `async with` 로 가짜 세션 하나를 준다."""

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
def jobs(monkeypatch: pytest.MonkeyPatch, store: Store, storage: LocalBridgeStorage) -> Iterator[list]:
    collected: list = []
    monkeypatch.setattr(service, "_spawn", collected.append)
    monkeypatch.setattr(service, "_session_factory", _SessionFactory())
    monkeypatch.setattr(settings, "cardimage_gemini_api_key", SecretStr("test-key"))
    monkeypatch.setattr(settings, "cardimage_months", frozenset({4, 9}))
    monkeypatch.setattr(settings, "cardimage_daily_limit", 1)
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: FakeEngine())
    monkeypatch.setattr(ai_card_engine, "default_judge", lambda: FakeJudge([5]))
    yield collected
    # 시작만 하고 안 돌린 코루틴을 그냥 두면 GC 때 RuntimeWarning 이 난다 — 여기서 닫아 정리한다.
    for job in collected:
        job.close()


def _photo() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), (1, 2, 3)).save(buf, "JPEG")
    return buf.getvalue()


def _start(**kw) -> AiCard:
    args = dict(photo=_photo(), content_type="image/jpeg", month=4, dog_name="네오", dog_id=None)
    args.update(kw)
    return asyncio.run(service.start(FakeSession(), OWNER, **args))


def _run_all(jobs: list) -> None:
    while jobs:
        asyncio.run(jobs.pop(0))


def test_start_then_background_makes_ready_card(store, storage, jobs) -> None:
    card = _start()
    assert card.status == "generating" and card.title == "BLOSSOM 네오"
    assert len(jobs) == 1 and store.ai_cards == [card]

    _run_all(jobs)

    assert card.status == "ready" and card.error_code is None
    assert (card.width, card.height) == (994, 1582)
    assert card.likeness == 5 and card.attempts == 1
    assert card.storage_key == f"ai-cards/{OWNER}/{card.id}.png"
    assert Image.open(storage.local_path(card.storage_key)).size == (994, 1582)

    got, url = asyncio.run(service.get_card(FakeSession(), OWNER, card.id))
    assert got is card and "/app/ai-cards/_bridge/download/" in url


def test_engine_failure_marks_failed_without_object(store, storage, jobs, monkeypatch) -> None:
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: FakeEngine(error=EngineError("upstream", "x")))
    card = _start()
    _run_all(jobs)
    assert card.status == "failed" and card.error_code == "upstream"
    assert card.storage_key is None
    assert not storage.local_path(f"ai-cards/{OWNER}/{card.id}.png").exists()


def test_row_gone_before_slot_never_calls_engine(store, storage, jobs, monkeypatch) -> None:
    """큐에서 기다리는 동안 행이 이미 정리(failed/interrupted)됐으면 엔진을 부르지 않는다
    (`_claim_slot` — 돈이 나가는 호출 직전 재확인)."""
    engine = FakeEngine()
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: engine)
    card = _start()
    # 세마포어를 기다리는 사이에 정리 기준을 넘겨 다른 조회가 실패로 덮은 상태를 흉내 낸다.
    card.status, card.error_code = "failed", "interrupted"
    _run_all(jobs)
    assert engine.calls == []
    assert not storage.local_path(f"ai-cards/{OWNER}/{card.id}.png").exists()


def test_row_deleted_before_slot_never_calls_engine(store, storage, jobs, monkeypatch) -> None:
    """큐에서 기다리는 동안 카드가 삭제됐으면 엔진을 부르지 않는다 (`_claim_slot`)."""
    engine = FakeEngine()
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: engine)
    card = _start()
    asyncio.run(service.delete_card(FakeSession(), OWNER, card.id))
    _run_all(jobs)
    assert engine.calls == []
    assert not storage.local_path(f"ai-cards/{OWNER}/{card.id}.png").exists()


def test_failed_card_does_not_use_up_daily_limit(store, jobs, monkeypatch) -> None:
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: FakeEngine(error=EngineError("upstream", "x")))
    _start()
    _run_all(jobs)
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: FakeEngine())
    assert _start().status == "generating"


@pytest.mark.parametrize(
    ("patch", "error"),
    [
        (lambda m: m.setattr(settings, "cardimage_gemini_api_key", SecretStr("")), CardImageUnavailable),
        (lambda m: None, MonthNotOpenError),
    ],
)
def test_config_problems_create_no_row(store, jobs, monkeypatch, patch, error) -> None:
    patch(monkeypatch)
    month = 12 if error is MonthNotOpenError else 4
    with pytest.raises(error):
        _start(month=month)
    assert store.ai_cards == [] and jobs == []


def test_bad_photo_creates_no_row(store, jobs) -> None:
    with pytest.raises(PhotoError):
        _start(photo=b"nope")
    assert store.ai_cards == [] and jobs == []


def test_storage_not_configured_creates_no_row(store, jobs, monkeypatch) -> None:
    monkeypatch.setattr(service, "get_storage", lambda: NotConfiguredStorage())
    with pytest.raises(StorageNotConfiguredError):
        _start()
    assert store.ai_cards == [] and jobs == []


def test_strangers_dog_is_not_found(store, jobs) -> None:
    other = FakePet(app_user_id=STRANGER, name="남의집", breed="mix")
    store.pets.append(other)
    with pytest.raises(service.AiCardNotFoundError):
        _start(dog_id=other.id)
    assert store.ai_cards == []


def test_my_dog_is_linked(store, jobs) -> None:
    pet = FakePet(app_user_id=OWNER, name="네옹", breed="mix")
    store.pets.append(pet)
    assert _start(dog_id=pet.id).dog_id == pet.id


def test_second_start_while_generating_is_busy(store, jobs) -> None:
    _start()
    with pytest.raises(quota.AiCardBusyError):
        _start()


def test_unique_index_race_is_busy(store, jobs, monkeypatch) -> None:
    """한도 검사를 둘 다 통과한 동시 요청 — DB 의 부분 UNIQUE 가 막고 서비스가 409 로 바꾼다."""
    _start()

    async def _no_quota(*a, **kw):
        return None

    monkeypatch.setattr(service, "check_quota", _no_quota)
    with pytest.raises(quota.AiCardBusyError):
        _start()
    assert len(store.ai_cards) == 1


def test_ready_card_uses_up_daily_limit(store, jobs) -> None:
    _start()
    _run_all(jobs)
    with pytest.raises(quota.AiCardLimitError):
        _start()


def test_delete_while_generating_leaves_no_object(store, storage, jobs) -> None:
    card = _start()
    asyncio.run(service.delete_card(FakeSession(), OWNER, card.id))
    _run_all(jobs)
    assert store.ai_cards == []
    assert not storage.local_path(f"ai-cards/{OWNER}/{card.id}.png").exists()


def test_delete_ready_removes_object(store, storage, jobs) -> None:
    card = _start()
    _run_all(jobs)
    path = storage.local_path(card.storage_key)
    asyncio.run(service.delete_card(FakeSession(), OWNER, card.id))
    assert store.ai_cards == [] and not path.exists()


def test_stale_generating_reads_as_interrupted(store, jobs) -> None:
    old = datetime.now(UTC) - timedelta(minutes=10)
    card = AiCard(
        id=uuid.uuid4(), app_user_id=OWNER, month=4, dog_name="네오", title="BLOSSOM 네오",
        status="generating", created_at=old, updated_at=old,
    )
    store.ai_cards.append(card)
    got, url = asyncio.run(service.get_card(FakeSession(), OWNER, card.id))
    assert got.status == "failed" and got.error_code == "interrupted" and url is None


def test_strangers_card_is_not_found(store, jobs) -> None:
    card = _start()
    with pytest.raises(service.AiCardNotFoundError):
        asyncio.run(service.get_card(FakeSession(), STRANGER, card.id))
    with pytest.raises(service.AiCardNotFoundError):
        asyncio.run(service.delete_card(FakeSession(), STRANGER, card.id))


def test_list_is_newest_first(store, jobs, monkeypatch) -> None:
    monkeypatch.setattr(settings, "cardimage_daily_limit", 0)
    first = _start()
    _run_all(jobs)
    second = _start()
    second.created_at = first.created_at + timedelta(seconds=1)
    assert [c.id for c in asyncio.run(service.list_cards(FakeSession(), OWNER))] == [second.id, first.id]


def test_cleanup_for_owner_removes_rows_and_objects(store, storage, jobs) -> None:
    card = _start()
    _run_all(jobs)
    path = storage.local_path(card.storage_key)
    assert asyncio.run(service.cleanup_for_owner(FakeSession(), OWNER)) == 1
    assert store.ai_cards == [] and not path.exists()


def test_cleanup_without_objects_does_not_touch_storage(store, jobs, monkeypatch) -> None:
    """저장소가 꺼져 있다고 탈퇴가 막히면 안 된다 — 지울 객체가 없으면 저장소를 부르지 않는다."""
    _start()  # generating, 객체 없음
    monkeypatch.setattr(service, "get_storage", lambda: NotConfiguredStorage())
    assert asyncio.run(service.cleanup_for_owner(FakeSession(), OWNER)) == 1
