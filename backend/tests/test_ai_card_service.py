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
from sqlalchemy.exc import IntegrityError

from daengs_backend.config import settings
from daengs_backend.core import storage as storage_module
from daengs_backend.core.storage import (
    LocalBridgeStorage,
    NotConfiguredStorage,
    StorageNotConfiguredError,
)
from daengs_backend.models import AiCard, AiCardUsage
from daengs_backend.repositories import ai_card as ai_card_repo
from daengs_backend.repositories import app_user as app_user_repo
from daengs_backend.services import ai_card as service
from daengs_backend.services import ai_card_engine
from daengs_backend.services import ai_card_quota as quota
from daengs_cardimage import CardImageUnavailable
from daengs_cardimage.catalog import MonthNotOpenError
from daengs_cardimage.engine import EngineError
from daengs_cardimage.photo import PhotoError
from daengs_cardimage.title import title_text

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
    # 이 파일의 기존 테스트는 모두 "카드 한 장" 세상(#537·#543)을 본다 — 여러 장(#572 Task 4)은
    # 아래 전용 테스트에서만 pick_count 를 따로 올린다.
    monkeypatch.setattr(settings, "cardimage_pick_count", 1)
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
    args = {"photo": _photo(), "content_type": "image/jpeg", "month": 4, "dog_name": "네오", "dog_id": None}
    args.update(kw)
    return asyncio.run(service.start(FakeSession(), OWNER, **args))


class _SideEffectEngine(FakeEngine):
    """엔진 호출(돈이 나가는 자리) **도중에** 무언가를 하는 엔진 — `_claim_slot` 뒤·`_finish_ready` 앞."""

    def __init__(self, side_effect) -> None:
        super().__init__()
        self.side_effect = side_effect

    def generate(self, **kw) -> bytes:
        self.side_effect()
        return super().generate(**kw)


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


def test_row_deleted_mid_generation_leaves_no_object(store, storage, jobs, monkeypatch) -> None:
    """차례를 얻은 뒤 생성 중에 행이 지워지면 `_finish_ready` 가 방금 쓴 객체를 지운다."""
    card = _start()
    engine = _SideEffectEngine(lambda: store.ai_cards.remove(card))
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: engine)
    _run_all(jobs)
    assert len(engine.calls) == 1  # 차례는 얻었다 — 삭제는 엔진 호출 중에 일어났다
    assert store.ai_cards == []
    assert not storage.local_path(f"ai-cards/{OWNER}/{card.id}.png").exists()


def test_row_expired_mid_generation_leaves_no_object_and_keeps_failed(
    store, storage, jobs, monkeypatch
) -> None:
    """생성 중에 정리 기준이 지나 실패로 덮였으면 객체를 지우고 실패를 그대로 둔다."""
    card = _start()

    def expire() -> None:
        card.status, card.error_code = "failed", "interrupted"

    engine = _SideEffectEngine(expire)
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: engine)
    _run_all(jobs)
    assert len(engine.calls) == 1
    assert card.status == "failed" and card.error_code == "interrupted"
    assert card.storage_key is None
    assert not storage.local_path(f"ai-cards/{OWNER}/{card.id}.png").exists()


def test_claim_slot_stamps_updated_at_before_generation(store, jobs, monkeypatch) -> None:
    """정리 기준은 차례를 얻은 시각부터 — `_claim_slot` 이 엔진 호출 **전에** `updated_at` 을 찍는다."""
    card = _start()
    old = datetime(2026, 1, 1, tzinfo=UTC)
    card.updated_at = old
    seen: list[datetime] = []
    engine = _SideEffectEngine(lambda: seen.append(card.updated_at))
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: engine)
    _run_all(jobs)
    assert len(seen) == 1 and seen[0] > old


def test_finish_ready_db_failure_removes_stored_object(store, storage, jobs, monkeypatch) -> None:
    """객체를 저장한 뒤 완료 기록이 실패하면 고아를 남기지 않는다 (키를 아는 곳이 거기뿐)."""
    card = _start()

    async def _boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(service, "_finish_ready", _boom)
    _run_all(jobs)
    assert card.status == "generating"  # 정리 기준이 지나면 interrupted 가 된다
    assert not storage.local_path(f"ai-cards/{OWNER}/{card.id}.png").exists()


def test_withdrawn_user_creates_no_row(store, jobs, monkeypatch) -> None:
    async def _not_active(session, app_user_id):
        return None

    monkeypatch.setattr(app_user_repo, "get_active_for_update", _not_active)
    with pytest.raises(service.AiCardUserNotActiveError):
        _start()
    assert store.ai_cards == [] and jobs == []


def test_bad_photo_is_rejected_before_touching_the_db(store, jobs, monkeypatch) -> None:
    """사진은 사용자 잠금보다 먼저 — 잘못된 본문으로는 잠금·연결을 잡지 않는다."""
    locks: list = []

    async def _lock(session, app_user_id):
        locks.append(app_user_id)

    monkeypatch.setattr(app_user_repo, "get_active_for_update", _lock)
    with pytest.raises(PhotoError):
        _start(photo=b"nope")
    assert locks == []


def test_long_uppercased_title_is_clamped(store, jobs) -> None:
    """`ß`.upper() 는 `SS` — 40자 이름의 제목이 VARCHAR(80) 을 넘지 않게 자른다."""
    name = "ß" * 40
    assert len(title_text("BLOSSOM", name)) > 80  # 자르지 않으면 넘친다
    assert len(_start(dog_name=name).title) <= 80


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


def test_other_integrity_error_is_not_busy(store, jobs, monkeypatch) -> None:
    """부분 UNIQUE 가 아닌 제약 위반(예: FK)은 409 로 삼키지 않고 그대로 올린다."""

    def _fk_violation(session, card):
        raise IntegrityError("ai_cards_dog_id_fkey", None, Exception("violates foreign key ai_cards_dog_id_fkey"))

    monkeypatch.setattr(ai_card_repo, "add", _fk_violation)
    with pytest.raises(IntegrityError):
        _start()
    assert jobs == []


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


# ── #543 제품 규칙 ──────────────────────────────────────────────────────


def test_ready_records_usage(store, jobs) -> None:
    card = _start()
    assert store.ai_card_usage == []  # 시작만으로는 안 센다
    _run_all(jobs)
    assert len(store.ai_card_usage) == 1
    usage = store.ai_card_usage[0]
    assert isinstance(usage, AiCardUsage)
    assert usage.card_id == card.id and usage.app_user_id == OWNER and usage.used_at is not None


def test_failed_records_no_usage(store, jobs, monkeypatch) -> None:
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: FakeEngine(error=EngineError("upstream", "x")))
    _start()
    _run_all(jobs)
    assert store.ai_card_usage == []


def test_row_deleted_mid_generation_records_no_usage(store, jobs, monkeypatch) -> None:
    card = _start()
    monkeypatch.setattr(
        ai_card_engine, "default_engine", lambda: _SideEffectEngine(lambda: store.ai_cards.remove(card))
    )
    _run_all(jobs)
    assert store.ai_card_usage == []


def test_deleting_ready_card_does_not_give_limit_back(store, jobs) -> None:
    card = _start()
    _run_all(jobs)
    asyncio.run(service.delete_card(FakeSession(), OWNER, card.id))
    assert len(store.ai_card_usage) == 1  # 기록은 남는다
    with pytest.raises(quota.AiCardLimitError):
        _start()


def test_same_dog_same_month_is_taken_other_month_is_ok(store, jobs, monkeypatch) -> None:
    monkeypatch.setattr(settings, "cardimage_daily_limit", 0)
    pet = FakePet(app_user_id=OWNER, name="네옹", breed="mix")
    store.pets.append(pet)
    _start(dog_id=pet.id, month=4)
    _run_all(jobs)
    with pytest.raises(quota.AiCardMonthTakenError):
        _start(dog_id=pet.id, month=4)
    assert _start(dog_id=pet.id, month=9).status == "generating"


def test_other_dog_same_month_is_ok(store, jobs, monkeypatch) -> None:
    monkeypatch.setattr(settings, "cardimage_daily_limit", 0)
    first = FakePet(app_user_id=OWNER, name="첫째", breed="mix")
    second = FakePet(app_user_id=OWNER, name="둘째", breed="mix")
    store.pets.extend([first, second])
    _start(dog_id=first.id, month=4)
    _run_all(jobs)
    assert _start(dog_id=second.id, month=4).status == "generating"


def test_deleting_month_card_reopens_month(store, jobs, monkeypatch) -> None:
    monkeypatch.setattr(settings, "cardimage_daily_limit", 0)
    pet = FakePet(app_user_id=OWNER, name="네옹", breed="mix")
    store.pets.append(pet)
    card = _start(dog_id=pet.id, month=4)
    _run_all(jobs)
    asyncio.run(service.delete_card(FakeSession(), OWNER, card.id))
    assert _start(dog_id=pet.id, month=4).status == "generating"


def test_title_name_changes_title_only(store, jobs) -> None:
    card = _start(title_name="  KONG   CHAN ")
    assert card.title == "BLOSSOM KONG CHAN"
    assert card.dog_name == "네오"


def test_blank_title_name_falls_back_to_dog_name(store, jobs) -> None:
    assert _start(title_name="   ").title == "BLOSSOM 네오"


def test_title_name_is_uppercased(store, jobs) -> None:
    assert _start(title_name="kong").title == "BLOSSOM KONG"


def test_long_uppercased_title_name_is_clamped(store, jobs) -> None:
    """`ß`.upper() 는 `SS` — 40자 `title_name` 의 제목도 VARCHAR(80) 을 넘지 않게 자른다."""
    assert len(_start(title_name="ß" * 40).title) <= 80


def _spy_generate(recorded: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """`ai_card_engine.generate` 를 가로채 `dog_name` 인자와 실제로 그려진 제목을 기록한다.
    `_run` 이 `ai_card_engine.generate` 를 속성으로 부르므로(모듈째 import) 여기서 바꿔치기가 먹는다."""
    original = ai_card_engine.generate

    def spy(**kw):
        recorded["dog_name"] = kw["dog_name"]
        generated = original(**kw)
        recorded["title"] = generated.title
        return generated

    monkeypatch.setattr(ai_card_engine, "generate", spy)


def test_title_name_reaches_generation(store, jobs, monkeypatch) -> None:
    """`title_name` 이 그림 제목에도 넘어가야 한다 — 저장된 `title` 과 그림에 그려진 제목이 같아야 한다."""
    recorded: dict = {}
    _spy_generate(recorded, monkeypatch)
    card = _start(title_name="kong")
    _run_all(jobs)
    assert recorded["dog_name"] == "kong"
    assert recorded["title"] == card.title


def test_no_title_name_generates_with_dog_name(store, jobs, monkeypatch) -> None:
    """`title_name` 이 없으면 `dog_name`(정규화된 값)이 그림 제목에도 그대로 간다."""
    recorded: dict = {}
    _spy_generate(recorded, monkeypatch)
    _start()
    _run_all(jobs)
    assert recorded["dog_name"] == "네오"


def test_daily_status(store, jobs, monkeypatch) -> None:
    assert asyncio.run(service.daily_status(FakeSession(), OWNER)) == (1, 1)
    _start()
    _run_all(jobs)
    assert asyncio.run(service.daily_status(FakeSession(), OWNER)) == (1, 0)
    monkeypatch.setattr(settings, "cardimage_daily_limit", 0)
    assert asyncio.run(service.daily_status(FakeSession(), OWNER)) == (None, None)


def test_cleanup_for_owner_keeps_only_todays_usage(store, jobs) -> None:
    """탈퇴는 어제 이전 기록만 지운다 — 오늘(KST) 기록을 지우면 재로그인으로 하루 한도가 초기화된다."""
    card = _start()
    _run_all(jobs)
    today = store.ai_card_usage[0]
    old = AiCardUsage(card_id=uuid.uuid4(), app_user_id=OWNER, used_at=datetime.now(UTC) - timedelta(days=2))
    stranger = AiCardUsage(card_id=uuid.uuid4(), app_user_id=STRANGER, used_at=datetime.now(UTC) - timedelta(days=2))
    store.ai_card_usage.extend([old, stranger])
    assert asyncio.run(service.cleanup_for_owner(FakeSession(), OWNER)) == 1
    assert store.ai_card_usage == [today, stranger]
    assert card not in store.ai_cards


def test_withdraw_then_relogin_same_day_keeps_limit(store, jobs) -> None:
    """같은 카카오 계정으로 다시 들어오면 같은 app_user_id 다 — 그날 한도는 그대로여야 한다."""
    _start()
    _run_all(jobs)
    asyncio.run(service.cleanup_for_owner(FakeSession(), OWNER))
    with pytest.raises(quota.AiCardLimitError):
        _start()


def test_cleanup_kst_boundary(store, jobs) -> None:
    """경계는 KST 자정 — 탈퇴 시각의 KST 오늘 00:00 이전 기록만 지운다."""
    now = datetime(2026, 9, 14, 3, 0, tzinfo=UTC)  # KST 12:00
    before_midnight = AiCardUsage(card_id=uuid.uuid4(), app_user_id=OWNER, used_at=datetime(2026, 9, 13, 14, 59, tzinfo=UTC))
    at_midnight = AiCardUsage(card_id=uuid.uuid4(), app_user_id=OWNER, used_at=datetime(2026, 9, 13, 15, 0, tzinfo=UTC))
    store.ai_card_usage.extend([before_midnight, at_midnight])
    asyncio.run(service.cleanup_for_owner(FakeSession(), OWNER, now=now))
    assert store.ai_card_usage == [at_midnight]


# ── #572 Task 4 — 한 요청에 2장, 고른 한 장만 저장 ──────────────────────


class _FailSecondCallEngine(FakeEngine):
    """두 번째 호출만 실패하는 엔진 — "한 장 실패해도 나머지 한 장은 남는다" 테스트용."""

    def generate(self, *, template_png, photo_jpeg, prompt, seed=None):
        self.calls.append({"template": template_png, "photo": photo_jpeg, "prompt": prompt, "seed": seed})
        if len(self.calls) == 2:
            raise EngineError("upstream", "두 번째 호출 실패")
        return self.outputs[0]


def test_start_creates_all_rows_up_front(store, jobs, monkeypatch) -> None:
    """행은 `start` 가 전부 미리 만든다(controller ruling A) — 백그라운드가 돌기 전에도 둘 다 있다.

    대표 행은 자기 id 를 `pick_group` 으로 쓴다 — `idx_ai_cards_one_generating` 이 그 한 행만
    보게 하기 위해서다(fix round 1 Critical)."""
    monkeypatch.setattr(settings, "cardimage_pick_count", 2)
    card = _start()
    assert card.pick_group == card.id
    assert len(store.ai_cards) == 2
    primary, sibling = store.ai_cards
    assert primary is card and primary.status == "generating" and sibling.status == "generating"
    assert sibling.pick_group == primary.pick_group
    assert sibling.id != primary.id and sibling.seed != primary.seed
    assert sibling.app_user_id == OWNER and sibling.dog_name == "네오" and sibling.title == card.title
    assert len(jobs) == 1  # 요청 하나에 백그라운드 작업 하나 — 그 안에서 행마다 순서대로 돈다


def test_two_cards_become_ready_and_only_first_records_usage(store, storage, jobs, monkeypatch) -> None:
    """두 장 다 성공하면 둘 다 `ready` 가 된다 — 사용 기록은 한 번만(요청을 센다, 카드 수가 아니다)."""
    monkeypatch.setattr(settings, "cardimage_pick_count", 2)
    card = _start()
    _run_all(jobs)

    assert len(store.ai_cards) == 2
    primary, sibling = store.ai_cards
    assert primary is card and primary.status == "ready" and sibling.status == "ready"
    assert sibling.storage_key != primary.storage_key
    assert Image.open(storage.local_path(sibling.storage_key)).size == (994, 1582)
    assert len(store.ai_card_usage) == 1 and store.ai_card_usage[0].card_id == primary.id


def test_second_generation_failing_leaves_one_ready_and_one_failed(store, storage, jobs, monkeypatch) -> None:
    """두 장 중 하나만 성공해도 실패로 떨어지지 않는다 — 실패한 행도 남는다(지워지지 않는다),
    고를 카드는 하나 있다."""
    monkeypatch.setattr(settings, "cardimage_pick_count", 2)
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: _FailSecondCallEngine())
    card = _start()
    _run_all(jobs)

    assert len(store.ai_cards) == 2
    primary, sibling = store.ai_cards
    assert primary is card and primary.status == "ready"
    assert sibling.status == "failed" and sibling.error_code == "upstream"
    assert len(store.ai_card_usage) == 1


def test_one_seed_month_makes_exactly_one_row_and_one_card(store, jobs, monkeypatch) -> None:
    """겹치지 않는 seed 가 하나뿐인 달은 `cardimage_pick_count=2` 여도 행을 하나만 만든다
    (controller ruling B — 같은 이미지 두 장에 돈을 두 번 내지 않는다)."""
    import dataclasses

    from daengs_cardimage import catalog

    monkeypatch.setattr(settings, "cardimage_pick_count", 2)
    monkeypatch.setitem(catalog._CARDS, 4, dataclasses.replace(catalog.get(4), seeds=(7,)))
    card = _start()
    assert len(store.ai_cards) == 1 and card.seed == 7
    _run_all(jobs)
    assert len(store.ai_cards) == 1 and card.status == "ready"


def test_deleting_generating_card_stops_the_next_card_and_records_no_usage(
    store, storage, jobs, monkeypatch
) -> None:
    """생성 중에(첫 장이 아직 도는 사이) 카드를 지우면 형제도 같이 지워지고, `_run` 이 그 형제를
    다시 만들지 않는다 — 취소된 요청은 만들지 않는다(#572 Task 4 fix round 1 Critical)."""
    monkeypatch.setattr(settings, "cardimage_pick_count", 2)
    engine = FakeEngine()
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: engine)
    card = _start()
    assert len(store.ai_cards) == 2
    asyncio.run(service.delete_card(FakeSession(), OWNER, card.id))
    assert store.ai_cards == []  # 형제도 같이 지워졌다

    _run_all(jobs)

    assert engine.calls == []  # 엔진이 한 번도 안 불렸다 — 돈이 안 나갔다
    assert store.ai_card_usage == []


def test_deleting_ready_card_does_not_cancel_generating_sibling(store, storage, jobs, monkeypatch) -> None:
    """카드 1이 이미 `ready` 면 아직 `generating` 인 카드 2 를 지우지 않는다(#572 Task 4 fix
    round 2 R2-2) — 하루 한도는 이미 그 `ready` 카드로 다 썼으므로 형제를 지워도 한도가
    돌아오지 않고, `month_taken` 도 그대로다. 오히려 지우면 사용자에게 카드가 하나도 안 남을
    수 있다."""
    monkeypatch.setattr(settings, "cardimage_pick_count", 2)
    _start()
    primary, sibling = store.ai_cards
    primary.status = "ready"  # 실제로 만들어졌다고 흉내 낸다 — sibling 은 아직 generating.

    asyncio.run(service.delete_card(FakeSession(), OWNER, primary.id))

    assert store.ai_cards == [sibling]  # 형제는 살아 있다
    assert sibling.status == "generating"


def test_group_progress_before_and_after_generation(store, storage, jobs, monkeypatch) -> None:
    monkeypatch.setattr(settings, "cardimage_pick_count", 2)
    card = _start()
    assert asyncio.run(service.group_progress(FakeSession(), OWNER, card)) == (0, 2, False)
    _run_all(jobs)
    assert asyncio.run(service.group_progress(FakeSession(), OWNER, card)) == (2, 2, True)


def test_finished_is_false_while_generating_and_true_once_second_card_fails(
    store, storage, jobs, monkeypatch
) -> None:
    """`finished` 는 `done == total` 이 아니라 "더 만들 카드가 없다" 를 본다 — 둘째 장이
    실패해도(`done` 이 영영 `total` 에 못 미쳐도) `finished` 는 참이 된다(fix round 1 Important 1)."""
    monkeypatch.setattr(settings, "cardimage_pick_count", 2)
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: _FailSecondCallEngine())
    card = _start()
    assert asyncio.run(service.group_progress(FakeSession(), OWNER, card))[2] is False

    _run_all(jobs)

    done, total, finished = asyncio.run(service.group_progress(FakeSession(), OWNER, card))
    assert (done, total, finished) == (1, 2, True)


def test_multi_row_request_still_blocks_a_concurrent_request(store, jobs, monkeypatch) -> None:
    """`has_generating` 은 행 수가 아니라 "generating 인 행이 있나" 를 본다 — 대표 행이든
    형제 행이든 하나라도 있으면 여전히 막는다."""
    monkeypatch.setattr(settings, "cardimage_pick_count", 2)
    _start()
    with pytest.raises(quota.AiCardBusyError):
        _start()


def test_multi_row_request_still_takes_the_month_once_ready(store, jobs, monkeypatch) -> None:
    """두 장 다 `ready` 가 된 뒤에도 `has_month_card` 는 여전히 그 달을 막는다 — 행이 여럿이어도
    조건(같은 dog_id·month·ready/generating)을 만족하는 행이 하나라도 있으면 걸린다."""
    monkeypatch.setattr(settings, "cardimage_daily_limit", 0)
    monkeypatch.setattr(settings, "cardimage_pick_count", 2)
    pet = FakePet(app_user_id=OWNER, name="네옹", breed="mix")
    store.pets.append(pet)
    _start(dog_id=pet.id, month=4)
    _run_all(jobs)
    with pytest.raises(quota.AiCardMonthTakenError):
        _start(dog_id=pet.id, month=4)


def test_expire_generating_expires_every_row_of_an_abandoned_group(store, jobs, monkeypatch) -> None:
    """정리 기준을 넘기면 그룹의 행 **전부**가 `failed`/`interrupted` 가 된다 — 대표 행만이 아니다."""
    monkeypatch.setattr(settings, "cardimage_pick_count", 2)
    card = _start()
    primary, sibling = store.ai_cards
    old = datetime.now(UTC) - timedelta(minutes=10)
    primary.updated_at = old
    sibling.updated_at = old

    asyncio.run(service.list_cards(FakeSession(), OWNER))

    assert primary.status == "failed" and primary.error_code == "interrupted"
    assert sibling.status == "failed" and sibling.error_code == "interrupted"
    assert card is primary


def test_group_progress_is_finished_without_pick_group(store, jobs) -> None:
    """옛 카드(마이그레이션 이전)는 `pick_group` 이 없다 — 기다릴 그룹이 없으니 `finished=True`."""
    card = AiCard(
        id=uuid.uuid4(), app_user_id=OWNER, month=4, dog_name="네오", title="BLOSSOM 네오", status="ready",
    )
    assert asyncio.run(service.group_progress(FakeSession(), OWNER, card)) == (None, None, True)


def test_choose_keeps_picked_card_and_deletes_sibling_object(store, storage, jobs, monkeypatch) -> None:
    monkeypatch.setattr(settings, "cardimage_pick_count", 2)
    _start()
    _run_all(jobs)
    primary, sibling = store.ai_cards
    sibling_path = storage.local_path(sibling.storage_key)
    assert sibling_path.exists()

    chosen, url = asyncio.run(service.choose_card(FakeSession(), OWNER, primary.id))

    assert chosen is primary
    assert store.ai_cards == [primary]
    assert not sibling_path.exists()
    assert "/app/ai-cards/_bridge/download/" in url


def test_choose_without_siblings_is_a_no_op(store, storage, jobs) -> None:
    """형제가 없으면(단일 생성 경로) 고른 카드를 그대로 돌려준다."""
    card = _start()
    _run_all(jobs)
    chosen, _url = asyncio.run(service.choose_card(FakeSession(), OWNER, card.id))
    assert chosen is card and store.ai_cards == [card]


def test_choose_strangers_card_is_not_found(store, jobs) -> None:
    card = _start()
    with pytest.raises(service.AiCardNotFoundError):
        asyncio.run(service.choose_card(FakeSession(), STRANGER, card.id))


def test_choose_a_failed_card_is_rejected_and_deletes_nothing(store, storage, jobs, monkeypatch) -> None:
    """#572 Task 4 fix round 2 R2-3 — `failed` 카드를 고르면 형제(그중 `ready` 인 좋은 카드일
    수 있다)를 지워 사용자에게 카드가 하나도 안 남을 수 있다. 그래서 막는다."""
    monkeypatch.setattr(settings, "cardimage_pick_count", 2)
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: _FailSecondCallEngine())
    _start()
    _run_all(jobs)
    primary, sibling = store.ai_cards
    assert primary.status == "ready" and sibling.status == "failed"

    with pytest.raises(service.AiCardNotReadyError):
        asyncio.run(service.choose_card(FakeSession(), OWNER, sibling.id))

    assert store.ai_cards == [primary, sibling]  # 아무것도 안 지워졌다


def test_choose_a_generating_card_is_rejected_and_deletes_nothing(store, storage, jobs, monkeypatch) -> None:
    """#572 Task 4 fix round 2 R2-3 — 아직 `generating` 인(형제가 먼저 `ready` 가 됐을 수 있는)
    카드를 고르면 그 `ready` 형제를 지워 버릴 수 있다. 그래서 막는다."""
    monkeypatch.setattr(settings, "cardimage_pick_count", 2)
    _start()
    primary, sibling = store.ai_cards
    primary.status = "ready"  # sibling 은 아직 generating 인 채로 둔다.

    with pytest.raises(service.AiCardNotReadyError):
        asyncio.run(service.choose_card(FakeSession(), OWNER, sibling.id))

    assert store.ai_cards == [primary, sibling]  # 아무것도 안 지워졌다
