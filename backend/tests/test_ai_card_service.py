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
from daengs_cardimage.engine import EngineError, HttpCardImageEngine
from daengs_cardimage.photo import PhotoError
from daengs_cardimage.title import title_text

OWNER = uuid.uuid4()
STRANGER = uuid.uuid4()

#: `jobs` 픽스처가 가짜로 바꾸기 **전의** 진짜 `default_engine` — 엔진 선택이 장수·seed 기록과 같은
#: 판정을 쓰는지 보는 테스트(#572 Task 8)가 부른다. 모듈을 읽는 시점에 잡아 둔다.
_REAL_DEFAULT_ENGINE = ai_card_engine.default_engine


def _two_card_gpu_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """한 요청에 두 장을 만드는 경로를 켠다 — `FLUX.2-klein-4B` GPU 경로(`cardgen_url` 있음)에서만
    `cardimage_pick_count` 장을 만든다(#572 Task 8, 사용자 결정 2026-09-17). Nano Banana 2 경로는
    설정값과 상관없이 한 장이다."""
    monkeypatch.setattr(settings, "cardimage_pick_count", 2)
    monkeypatch.setattr(settings, "cardgen_url", "http://cardgen.example")


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
    # 기본은 지금 운영과 같은 Nano Banana 2 경로(`cardgen_url` 빈 값, #572 Task 8) — `.env` 에 무엇이
    # 있든 이 파일의 결과가 달라지지 않게 못박는다. 두 장 경로는 `_two_card_gpu_path` 로 켠다.
    monkeypatch.setattr(settings, "cardgen_url", "")
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
    # `month=4` 로 부르는 기존 호출을 그대로 둔다 — `service.start` 의 인자는 `card` 하나이고
    # 달 정수는 그 선택자의 한 갈래다 (#593, D-085). 종류 카드는 `card="strawberry"` 로 부른다.
    if "month" in kw:
        kw["card"] = kw.pop("month")
    args = {"photo": _photo(), "content_type": "image/jpeg", "card": 4, "dog_name": "네오", "dog_id": None}
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


def test_ready_card_records_seed_when_cardgen_url_is_set(store, storage, jobs, monkeypatch) -> None:
    """GPU 엔진(`cardgen_url` 있음)은 seed 를 실제로 쓰므로 그대로 기록한다(최종 리뷰 minor 3)."""
    monkeypatch.setattr(settings, "cardgen_url", "http://cardgen.example")
    card = _start()
    drawn_seed = card.seed
    assert drawn_seed is not None
    _run_all(jobs)
    assert card.status == "ready" and card.seed == drawn_seed


def test_ready_card_seed_is_none_when_cardgen_url_is_empty(store, storage, jobs) -> None:
    """Nano Banana 2(`cardgen_url` 빈 값)는 seed 를 버리므로 거짓 기록을 남기지 않는다."""
    card = _start()
    # #572 Task 8 — 이 경로는 seed 를 명시하지 않고 부른다(재시도 경로). 행에도 처음부터 없다.
    assert card.seed is None
    _run_all(jobs)
    assert card.status == "ready" and card.seed is None


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
        id=uuid.uuid4(), app_user_id=OWNER, month=4, card_key="4", dog_name="네오", title="BLOSSOM 네오",
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


def test_failed_records_no_usage_but_an_attempt_mark(store, jobs, monkeypatch) -> None:
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: FakeEngine(error=EngineError("upstream", "x")))
    card = _start()
    _run_all(jobs)
    assert [(u.card_id, u.unfulfilled_attempt) for u in store.ai_card_usage] == [(card.pick_group, True)]
    assert asyncio.run(service.daily_status(FakeSession(), OWNER)) == (1, 1)


def test_row_deleted_mid_generation_records_no_usage_but_an_attempt_mark(store, jobs, monkeypatch) -> None:
    card = _start()
    monkeypatch.setattr(
        ai_card_engine, "default_engine", lambda: _SideEffectEngine(lambda: store.ai_cards.remove(card))
    )
    _run_all(jobs)
    assert [(u.card_id, u.unfulfilled_attempt) for u in store.ai_card_usage] == [(card.pick_group, True)]


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
    with pytest.raises(quota.AiCardTakenError):
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
    _two_card_gpu_path(monkeypatch)
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
    _two_card_gpu_path(monkeypatch)
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
    _two_card_gpu_path(monkeypatch)
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

    _two_card_gpu_path(monkeypatch)
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
    _two_card_gpu_path(monkeypatch)
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
    _two_card_gpu_path(monkeypatch)
    _start()
    primary, sibling = store.ai_cards
    primary.status = "ready"  # 실제로 만들어졌다고 흉내 낸다 — sibling 은 아직 generating.

    asyncio.run(service.delete_card(FakeSession(), OWNER, primary.id))

    assert store.ai_cards == [sibling]  # 형제는 살아 있다
    assert sibling.status == "generating"


def test_group_progress_before_and_after_generation(store, storage, jobs, monkeypatch) -> None:
    _two_card_gpu_path(monkeypatch)
    card = _start()
    assert asyncio.run(service.group_progress(FakeSession(), OWNER, card)) == (0, 2, False)
    _run_all(jobs)
    assert asyncio.run(service.group_progress(FakeSession(), OWNER, card)) == (2, 2, True)


def test_finished_is_false_while_generating_and_true_once_second_card_fails(
    store, storage, jobs, monkeypatch
) -> None:
    """`finished` 는 `done == total` 이 아니라 "더 만들 카드가 없다" 를 본다 — 둘째 장이
    실패해도(`done` 이 영영 `total` 에 못 미쳐도) `finished` 는 참이 된다(fix round 1 Important 1)."""
    _two_card_gpu_path(monkeypatch)
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: _FailSecondCallEngine())
    card = _start()
    assert asyncio.run(service.group_progress(FakeSession(), OWNER, card))[2] is False

    _run_all(jobs)

    done, total, finished = asyncio.run(service.group_progress(FakeSession(), OWNER, card))
    assert (done, total, finished) == (1, 2, True)


def test_multi_row_request_still_blocks_a_concurrent_request(store, jobs, monkeypatch) -> None:
    """`has_generating` 은 행 수가 아니라 "generating 인 행이 있나" 를 본다 — 대표 행이든
    형제 행이든 하나라도 있으면 여전히 막는다."""
    _two_card_gpu_path(monkeypatch)
    _start()
    with pytest.raises(quota.AiCardBusyError):
        _start()


def test_multi_row_request_still_takes_the_month_once_ready(store, jobs, monkeypatch) -> None:
    """두 장 다 `ready` 가 된 뒤에도 `has_month_card` 는 여전히 그 달을 막는다 — 행이 여럿이어도
    조건(같은 dog_id·month·ready/generating)을 만족하는 행이 하나라도 있으면 걸린다."""
    monkeypatch.setattr(settings, "cardimage_daily_limit", 0)
    _two_card_gpu_path(monkeypatch)
    pet = FakePet(app_user_id=OWNER, name="네옹", breed="mix")
    store.pets.append(pet)
    _start(dog_id=pet.id, month=4)
    _run_all(jobs)
    with pytest.raises(quota.AiCardTakenError):
        _start(dog_id=pet.id, month=4)


def test_expire_generating_expires_every_row_of_an_abandoned_group(store, jobs, monkeypatch) -> None:
    """정리 기준을 넘기면 그룹의 행 **전부**가 `failed`/`interrupted` 가 된다 — 대표 행만이 아니다."""
    _two_card_gpu_path(monkeypatch)
    card = _start()
    primary, sibling = store.ai_cards
    # GPU 경로의 정리 기준은 `cardgen_timeout_s` 만큼 길다(#572 Task 8 로 두 장이 그 경로에서만 나온다) —
    # 고정 10분이 아니라 그 기준을 넘긴 시각으로 둔다.
    old = datetime.now(UTC) - quota.stale_after() - timedelta(minutes=1)
    primary.updated_at = old
    sibling.updated_at = old

    asyncio.run(service.list_cards(FakeSession(), OWNER))

    assert primary.status == "failed" and primary.error_code == "interrupted"
    assert sibling.status == "failed" and sibling.error_code == "interrupted"
    assert card is primary


def test_group_progress_is_finished_without_pick_group(store, jobs) -> None:
    """옛 카드(마이그레이션 이전)는 `pick_group` 이 없다 — 기다릴 그룹이 없으니 `finished=True`."""
    card = AiCard(
        id=uuid.uuid4(), app_user_id=OWNER, month=4, card_key="4", dog_name="네오", title="BLOSSOM 네오", status="ready",
    )
    assert asyncio.run(service.group_progress(FakeSession(), OWNER, card)) == (None, None, True)


def test_choose_keeps_picked_card_and_deletes_sibling_object(store, storage, jobs, monkeypatch) -> None:
    _two_card_gpu_path(monkeypatch)
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
    _two_card_gpu_path(monkeypatch)
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: _FailSecondCallEngine())
    _start()
    _run_all(jobs)
    primary, sibling = store.ai_cards
    assert primary.status == "ready" and sibling.status == "failed"

    with pytest.raises(service.AiCardNotReadyError):
        asyncio.run(service.choose_card(FakeSession(), OWNER, sibling.id))

    assert store.ai_cards == [primary, sibling]  # 아무것도 안 지워졌다


def test_follower_is_not_expired_while_the_leader_is_still_generating(store, storage, jobs, monkeypatch) -> None:
    """#572 Task 5 — 요청 시각이 오래전이라(대기열이 길었다) 두 번째 카드의 `updated_at` 이 이미
    정리 기준을 넘었어도, 첫 카드가 슬롯을 잡고 도는 동안 누가 목록을 열면 두 번째를 덮으면 안 된다.
    덮으면 사용자는 돈 한 푼 안 나간 채 약속받은 두 장 중 한 장을 조용히 잃는다."""
    # 두 장은 GPU 경로에서만 나온다(#572 Task 8). `stale_after()` 는 그 경로의 예산(콜드 스타트 포함)으로
    # 아래에서 다시 재므로, 요청 시각은 여전히 정리 기준을 넘긴 채다.
    _two_card_gpu_path(monkeypatch)
    requested = datetime.now(UTC) - quota.stale_after() - timedelta(minutes=1)
    _start(now=requested)
    primary, sibling = store.ai_cards
    engine = _SideEffectEngine(lambda: asyncio.run(service.list_cards(FakeSession(), OWNER)))
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: engine)

    _run_all(jobs)

    assert len(engine.calls) == 2  # 두 번째 카드도 실제로 만들어졌다
    assert primary.status == "ready" and sibling.status == "ready"


def _judge_scores(monkeypatch: pytest.MonkeyPatch, scores: list) -> None:
    monkeypatch.setattr(settings, "cardimage_judge_min", 3)
    monkeypatch.setattr(ai_card_engine, "default_judge", lambda: FakeJudge(scores))


def test_below_min_card_is_ready_but_does_not_use_the_daily_limit(store, jobs, monkeypatch) -> None:
    """닮음이 기준 미만이면 카드는 돌려주되 하루치는 안 쓴다 — 사진 각도가 나쁘면 다시 뽑아도
    안 구해지므로(#557 E2 엎드린 옆모습 0장) 사용자가 그날을 통째로 잃는다. 대신 시도 표시가 남는다."""
    _judge_scores(monkeypatch, [2])
    card = _start()
    _run_all(jobs)
    assert card.status == "ready" and card.likeness == 2
    assert [(u.card_id, u.unfulfilled_attempt) for u in store.ai_card_usage] == [(card.pick_group, True)]
    assert asyncio.run(service.daily_status(FakeSession(), OWNER)) == (1, 1)
    assert _start(month=9).status == "generating"


def test_card_at_the_threshold_uses_the_daily_limit(store, jobs, monkeypatch) -> None:
    _judge_scores(monkeypatch, [3])
    card = _start()
    _run_all(jobs)
    assert [(u.card_id, u.unfulfilled_attempt) for u in store.ai_card_usage] == [(card.id, False)]
    with pytest.raises(quota.AiCardLimitError):
        _start()


def test_judge_outage_uses_the_daily_limit(store, jobs, monkeypatch) -> None:
    """검수 장애로 점수가 없으면(`None`) 기준을 넘은 것으로 센다 — 장애가 공짜 무한 생성이 되면 안 된다."""
    from daengs_cardimage.judge import JudgeError

    monkeypatch.setattr(ai_card_engine, "default_judge", lambda: FakeJudge(error=JudgeError("down")))
    card = _start()
    _run_all(jobs)
    assert card.status == "ready" and card.likeness is None
    assert [u.unfulfilled_attempt for u in store.ai_card_usage] == [False]
    with pytest.raises(quota.AiCardLimitError):
        _start()


def test_below_min_then_good_card_leaves_only_the_usage(store, jobs, monkeypatch) -> None:
    """한 요청에서 한 장이라도 기준을 넘으면 그 요청은 성공한 뽑기다 — 첫 슬롯에서 남긴 시도 표시는
    같은 트랜잭션에서 지우고 사용 기록 하나만 남는다(시도 상한에 이중으로 잡히지 않는다)."""
    _two_card_gpu_path(monkeypatch)
    _judge_scores(monkeypatch, [2, 5])
    _start()
    _run_all(jobs)
    primary, sibling = store.ai_cards
    assert primary.status == "ready" and sibling.status == "ready"
    assert [(u.card_id, u.unfulfilled_attempt) for u in store.ai_card_usage] == [(sibling.id, False)]


def test_good_then_below_min_card_leaves_only_the_usage(store, jobs, monkeypatch) -> None:
    _two_card_gpu_path(monkeypatch)
    _judge_scores(monkeypatch, [5, 2])
    card = _start()
    _run_all(jobs)
    assert [(u.card_id, u.unfulfilled_attempt) for u in store.ai_card_usage] == [(card.id, False)]


def test_two_below_min_cards_leave_one_mark_per_request(store, jobs, monkeypatch) -> None:
    """시도는 **요청 단위**로 센다 — 두 장이 다 미달이어도 뽑기 한 번이다."""
    _two_card_gpu_path(monkeypatch)
    _judge_scores(monkeypatch, [2])
    card = _start()
    _run_all(jobs)
    assert [(u.card_id, u.unfulfilled_attempt) for u in store.ai_card_usage] == [(card.pick_group, True)]


def test_deleting_below_min_cards_does_not_reset_the_paid_cap(store, jobs, monkeypatch) -> None:
    """5번 연속 미달이면 여섯 번째는 거절된다 — **카드를 지워도** 표시는 남는다. 같은 강아지·같은 달을
    다시 뽑으려면 그 카드를 지워야 하므로(`month_taken`), 카드 행으로 셌다면 매번 초기화됐다."""
    _two_card_gpu_path(monkeypatch)
    _judge_scores(monkeypatch, [1])
    pet = FakePet(app_user_id=OWNER, name="네옹", breed="mix")
    store.pets.append(pet)
    for _ in range(quota.MAX_PAID_FAILURES_PER_DAY):
        _start(dog_id=pet.id, month=4)
        _run_all(jobs)
        for card in list(store.ai_cards):
            asyncio.run(service.delete_card(FakeSession(), OWNER, card.id))
    assert store.ai_cards == []
    with pytest.raises(quota.AiCardLimitError):
        _start(dog_id=pet.id, month=4)


def test_start_then_delete_mid_generation_loop_is_bounded(store, storage, jobs, monkeypatch) -> None:
    """F1 (D-084) — 시작 → 유료 호출 도중 삭제를 되풀이하면 카드 행·사용 기록이 하나도 안 남는다. 시도
    표시가 **호출 전에** 남으므로 5번째 뒤로는 거절되고, 엔진은 정확히 5번만 불린다."""
    _two_card_gpu_path(monkeypatch)
    monkeypatch.setattr(settings, "cardimage_daily_limit", 1)
    current: dict = {}
    engine = _SideEffectEngine(lambda: asyncio.run(service.delete_card(FakeSession(), OWNER, current["id"])))
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: engine)
    for _ in range(quota.MAX_PAID_FAILURES_PER_DAY):
        current["id"] = _start().id
        _run_all(jobs)
        assert store.ai_cards == []  # 대표·형제 모두 지워졌다 — 카드 행으로는 셀 것이 없다
    assert [u.unfulfilled_attempt for u in store.ai_card_usage] == [True] * quota.MAX_PAID_FAILURES_PER_DAY
    with pytest.raises(quota.AiCardLimitError):
        _start()
    assert len(engine.calls) == quota.MAX_PAID_FAILURES_PER_DAY


def test_failed_calls_leave_one_mark_per_request_even_after_deleting_the_cards(store, jobs, monkeypatch) -> None:
    """두 장이 다 실패한 요청도 표시는 요청마다 한 줄이고, 실패 카드를 지워도 남는다."""
    _two_card_gpu_path(monkeypatch)
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: FakeEngine(error=EngineError("upstream", "x")))
    card = _start()
    _run_all(jobs)
    assert [c.status for c in store.ai_cards] == ["failed", "failed"]
    for c in list(store.ai_cards):
        asyncio.run(service.delete_card(FakeSession(), OWNER, c.id))
    assert [(u.card_id, u.unfulfilled_attempt) for u in store.ai_card_usage] == [(card.pick_group, True)]


def test_attempt_mark_is_written_before_the_paid_call(store, jobs, monkeypatch) -> None:
    seen: list = []
    engine = _SideEffectEngine(lambda: seen.append([(u.card_id, u.unfulfilled_attempt) for u in store.ai_card_usage]))
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: engine)
    card = _start()
    assert store.ai_card_usage == []  # 한도 검사를 통과한 것만으로는 안 남는다
    _run_all(jobs)
    assert seen == [[(card.pick_group, True)]]


def test_choose_a_generating_card_is_rejected_and_deletes_nothing(store, storage, jobs, monkeypatch) -> None:
    """#572 Task 4 fix round 2 R2-3 — 아직 `generating` 인(형제가 먼저 `ready` 가 됐을 수 있는)
    카드를 고르면 그 `ready` 형제를 지워 버릴 수 있다. 그래서 막는다."""
    _two_card_gpu_path(monkeypatch)
    _start()
    primary, sibling = store.ai_cards
    primary.status = "ready"  # sibling 은 아직 generating 인 채로 둔다.

    with pytest.raises(service.AiCardNotReadyError):
        asyncio.run(service.choose_card(FakeSession(), OWNER, sibling.id))

    assert store.ai_cards == [primary, sibling]  # 아무것도 안 지워졌다


# ── #572 Task 8 — 장수는 엔진이 정한다 (사용자 결정 2026-09-17) ──────────────
# Nano Banana 2(`cardgen_url` 빈 값, 지금 운영)는 한 요청에 **한 장**, seed 를 명시하지 않고 불러
# `generate_card` 의 재시도(첫 장이 `cardimage_judge_min` 미만이면 한 번 더)가 돈다. 두 장은 앱의 고르기
# 화면과 함께 `FLUX.2-klein-4B`(`cardgen_url` 있음)를 켤 때 나간다.


def _nano_banana_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """설정값이 2여도 Nano Banana 2 경로는 한 장이다 — 그것을 보이려고 일부러 2로 둔다."""
    monkeypatch.setattr(settings, "cardimage_pick_count", 2)
    monkeypatch.setattr(settings, "cardgen_url", "")


def _spy_generate_seeds(monkeypatch: pytest.MonkeyPatch) -> list:
    """`_run` 이 `ai_card_engine.generate` 에 넘긴 `seed` 인자를 호출마다 모은다."""
    seen: list = []
    original = ai_card_engine.generate

    def spy(**kw):
        seen.append(kw.get("seed"))
        return original(**kw)

    monkeypatch.setattr(ai_card_engine, "generate", spy)
    return seen


def test_nano_banana_path_creates_exactly_one_row_without_a_seed(store, jobs, monkeypatch) -> None:
    _nano_banana_path(monkeypatch)
    card = _start()
    assert store.ai_cards == [card] and card.seed is None and card.pick_group == card.id
    assert asyncio.run(service.group_progress(FakeSession(), OWNER, card)) == (0, 1, False)


def test_nano_banana_path_good_first_card_calls_the_engine_once(store, jobs, monkeypatch) -> None:
    _nano_banana_path(monkeypatch)
    _judge_scores(monkeypatch, [3])
    engine = FakeEngine()
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: engine)
    seeds = _spy_generate_seeds(monkeypatch)
    card = _start()
    _run_all(jobs)
    assert seeds == [None]  # seed 를 명시하지 않았다 — 재시도 경로다
    assert len(engine.calls) == 1
    assert card.status == "ready" and card.attempts == 1 and card.likeness == 3 and card.seed is None
    assert [(u.card_id, u.unfulfilled_attempt) for u in store.ai_card_usage] == [(card.id, False)]
    assert asyncio.run(service.group_progress(FakeSession(), OWNER, card)) == (1, 1, True)


def test_nano_banana_path_retries_once_when_the_first_card_is_below_the_bar(store, jobs, monkeypatch) -> None:
    """운영 경로의 재시도가 살아 있다 — 첫 장이 기준 미만이면 한 행 안에서 엔진을 한 번 더 부르고 나은 쪽을
    남긴다. 시도 표시는 **첫 호출 전에 한 번만** 남고(재시도 전에 다시 남지 않는다), 남긴 카드가 기준
    이상이면 사용 기록 하나로 바뀐다."""
    _nano_banana_path(monkeypatch)
    _judge_scores(monkeypatch, [2, 4])
    seen_marks: list = []
    engine = _SideEffectEngine(
        lambda: seen_marks.append([(u.card_id, u.unfulfilled_attempt) for u in store.ai_card_usage])
    )
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: engine)
    seeds = _spy_generate_seeds(monkeypatch)
    card = _start()
    _run_all(jobs)

    assert seeds == [None]  # `_run` 은 한 번 불렀고, 재시도는 `generate_card` 안에서 돌았다
    assert len(engine.calls) == 2
    assert seen_marks == [[(card.pick_group, True)], [(card.pick_group, True)]]
    assert store.ai_cards == [card]
    assert card.status == "ready" and card.attempts == 2 and card.likeness == 4 and card.seed is None
    assert [(u.card_id, u.unfulfilled_attempt) for u in store.ai_card_usage] == [(card.id, False)]
    assert asyncio.run(service.group_progress(FakeSession(), OWNER, card)) == (1, 1, True)


def test_nano_banana_path_retry_still_below_the_bar_keeps_the_mark(store, jobs, monkeypatch) -> None:
    _nano_banana_path(monkeypatch)
    _judge_scores(monkeypatch, [2, 1])
    engine = FakeEngine()
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: engine)
    card = _start()
    _run_all(jobs)
    assert len(engine.calls) == 2
    assert card.status == "ready" and card.attempts == 2 and card.likeness == 2  # 둘 중 나은 첫 장
    assert [(u.card_id, u.unfulfilled_attempt) for u in store.ai_card_usage] == [(card.pick_group, True)]


def test_nano_banana_path_delete_during_the_first_call_still_counts_once_and_leaves_nothing(
    store, storage, jobs, monkeypatch
) -> None:
    """운영 경로에도 시작→생성 중 삭제 구멍(F1, D-084)이 있다 — 재시도가 한 행 안에서 돌기 때문에, 첫 유료
    호출 도중 카드를 지워도 재시도 호출까지 나간다. 그래도 시도 표시는 호출 전에 한 줄만 남고(지워도 남는다),
    사용 기록은 없고, 저장한 객체도 남지 않아야 한다."""
    _nano_banana_path(monkeypatch)
    _judge_scores(monkeypatch, [2, 4])  # 첫 장 미달 → 재시도, 둘째 장은 기준 이상
    current: dict = {}
    calls = {"n": 0}

    def delete_on_first_call() -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            asyncio.run(service.delete_card(FakeSession(), OWNER, current["id"]))

    engine = _SideEffectEngine(delete_on_first_call)
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: engine)
    card = _start()
    current["id"] = card.id
    _run_all(jobs)

    assert len(engine.calls) == 2  # 삭제는 첫 호출 안에서였고, 재시도 호출까지 나갔다
    assert store.ai_cards == []
    assert [(u.card_id, u.unfulfilled_attempt) for u in store.ai_card_usage] == [(card.pick_group, True)]
    assert not storage.local_path(f"ai-cards/{OWNER}/{card.id}.png").exists()


def test_gpu_path_passes_explicit_seeds_and_never_retries(store, jobs, monkeypatch) -> None:
    """GPU 경로는 그대로다 — 행마다 미리 뽑은 seed 를 명시하고, 기준 미만이어도 재시도하지 않는다."""
    _two_card_gpu_path(monkeypatch)
    _judge_scores(monkeypatch, [2])
    engine = FakeEngine()
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: engine)
    seeds = _spy_generate_seeds(monkeypatch)
    _start()
    primary, sibling = store.ai_cards
    _run_all(jobs)
    assert seeds == [primary.seed, sibling.seed] and None not in seeds
    assert len(engine.calls) == 2 and primary.attempts == 1 and sibling.attempts == 1


@pytest.mark.parametrize(
    "url", ["http://cardgen.example", "  https://cardgen.example  ", "", "   "], ids=["set", "padded", "empty", "blank"]
)
def test_engine_choice_card_count_and_recorded_seed_agree(store, jobs, monkeypatch, url) -> None:
    """「GPU 경로인가」는 `ai_card_engine.gpu_path_active` 한 곳이 정한다 — 엔진 선택·장수·seed 기록이
    서로 다른 판정을 쓰면(예: 한쪽만 `strip()`) 공백뿐인 URL 에서 Nano Banana 2 로 두 장을 뽑거나
    엔진이 버린 seed 를 기록하게 된다."""
    monkeypatch.setattr(settings, "cardgen_url", url)
    monkeypatch.setattr(settings, "cardimage_pick_count", 2)
    gpu = ai_card_engine.gpu_path_active()
    assert gpu is bool(url.strip())
    assert isinstance(_REAL_DEFAULT_ENGINE(), HttpCardImageEngine) is gpu
    _start()
    assert len(store.ai_cards) == (2 if gpu else 1)
    _run_all(jobs)
    assert [c.status for c in store.ai_cards] == ["ready"] * len(store.ai_cards)
    assert all((c.seed is not None) is gpu for c in store.ai_cards)


# ── #593 (D-085) 종류 카드(딸기·상추)를 앱 경로에서도 만든다 ──────────────


def test_kind_card_row_has_null_month_and_a_kind_card_key(store, jobs) -> None:
    """종류 카드의 행은 `month IS NULL` · `card_key='strawberry'` 다 (`db/init/38_ai_cards.sql` 의
    CHECK `ai_cards_month`). ⚠ `daengs_cardimage` 는 종류 카드의 달을 **0** 으로 두는데, 그 0 을
    그대로 넣으면 CHECK 를 어긴다 — 서비스가 선택자에서 직접 만든다."""
    card = _start(card="strawberry")
    assert card.month is None and card.card_key == "strawberry"
    assert card.title == "BERRY 네오"
    assert store.ai_cards == [card]


def test_month_card_row_keeps_month_and_a_numeric_card_key(store, jobs) -> None:
    """달 카드는 지금까지와 같다 — `month=4` 이고 `card_key` 는 그 달의 문자열이어야 한다(CHECK 의 짝 규칙)."""
    card = _start(month=4)
    assert card.month == 4 and card.card_key == "4"


def test_kind_card_runs_to_ready(store, storage, jobs) -> None:
    card = _start(card="strawberry")
    _run_all(jobs)
    assert card.status == "ready" and card.error_code is None
    assert (card.width, card.height) == (994, 1582)
    assert Image.open(storage.local_path(card.storage_key)).size == (994, 1582)


def test_kind_card_passes_the_kind_to_the_engine(store, jobs, monkeypatch) -> None:
    """엔진에 달 정수가 아니라 종류가 가야 딸기 틀로 만들어진다 — 옛 코드는 달만 넘겼다(#592)."""
    seen: list = []
    real = ai_card_engine.generate

    def _spy(**kw):
        seen.append(kw["card"])
        return real(**kw)

    monkeypatch.setattr(ai_card_engine, "generate", _spy)
    _start(card="lettuce")
    _run_all(jobs)
    assert seen == ["lettuce"]


def test_a_dog_can_hold_a_month_card_and_a_kind_card_at_once(store, jobs, monkeypatch) -> None:
    """한도는 **카드 종류당 한 장**이다(사용자 결정 09-18) — 4월 카드가 딸기를 막지 않는다."""
    monkeypatch.setattr(settings, "cardimage_daily_limit", 0)
    pet = FakePet(app_user_id=OWNER, name="네옹", breed="mix")
    store.pets.append(pet)
    _start(dog_id=pet.id, month=4)
    _run_all(jobs)
    assert _start(dog_id=pet.id, card="strawberry").status == "generating"
    _run_all(jobs)
    assert {c.card_key for c in store.ai_cards} == {"4", "strawberry"}


def test_second_card_of_the_same_kind_is_taken(store, jobs, monkeypatch) -> None:
    monkeypatch.setattr(settings, "cardimage_daily_limit", 0)
    pet = FakePet(app_user_id=OWNER, name="네옹", breed="mix")
    store.pets.append(pet)
    _start(dog_id=pet.id, card="strawberry")
    _run_all(jobs)
    with pytest.raises(quota.AiCardTakenError):
        _start(dog_id=pet.id, card="strawberry")
    # 다른 종류는 그대로 열려 있다 — 둘 다 `month` 가 NULL 이라 달로 셌다면 여기서 막혔다.
    assert _start(dog_id=pet.id, card="lettuce").status == "generating"


def test_kinds_are_open_without_the_month_setting(store, jobs, monkeypatch) -> None:
    """종류 카드에는 `DAENGS_CARDIMAGE_MONTHS` 에 해당하는 잠금이 없다 — **카탈로그에 있으면 열린 것**이다
    (#593 에서 정함). 달은 여전히 그 설정이 가른다."""
    monkeypatch.setattr(settings, "cardimage_months", frozenset())
    with pytest.raises(MonthNotOpenError):
        _start(month=4)
    assert _start(card="strawberry").status == "generating"


def test_unknown_kind_is_rejected_before_any_row(store, jobs) -> None:
    with pytest.raises(MonthNotOpenError):
        _start(card="banana")
    assert store.ai_cards == [] and jobs == []
