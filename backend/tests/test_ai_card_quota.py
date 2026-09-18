"""`services/ai_card_quota.py` — 앱 사용자 AI 카드 생성 한도
(#537 · #543 · #572 · #593, D-076 · D-077 · D-084 · D-085).

제품 규칙(사용자 결정 2026-09-15): 동시 1장 · KST 하루 N회(**사용 기록**으로 셈 — 지워도 안 돌아옴,
실패는 안 셈) · 강아지마다 **카드 종류당** 한 장(보호자마다 따로).
#572(D-084): 좋은 카드를 못 얻은 유료 시도는 지울 수 없는 시도 표시로 하루 5요청까지 · 정리 기준은 요청
단위 마지막 진척부터 · GPU 경로면 콜드 스타트를 예산에 더한다.
#593(D-085): 종류 카드(딸기·상추)도 같은 규칙을 쓴다 — 세는 칸이 `month` 가 아니라 `card_key` 라
4월 카드가 딸기를 막지 않는다. 하루 한도는 달·종류가 **하나의 계수기**를 나눠 쓴다.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeAdmin, FakeAppUser, Store, install

from daengs_backend.config import Settings, settings
from daengs_backend.models import AiCard, AiCardUsage
from daengs_backend.services import ai_card_quota as quota

OWNER = uuid.uuid4()
STRANGER = uuid.uuid4()
DOG = uuid.uuid4()
OTHER_DOG = uuid.uuid4()
NOW = datetime(2026, 9, 14, 3, 0, tzinfo=UTC)  # KST 12:00
KST_TODAY_START = datetime(2026, 9, 13, 15, 0, tzinfo=UTC)  # KST 14일 00:00
KST_YESTERDAY_LAST = datetime(2026, 9, 13, 14, 59, tzinfo=UTC)  # KST 13일 23:59


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    s = install(Store(FakeAdmin()), monkeypatch)
    s.add_app_user(FakeAppUser(kakao_id=1, id=OWNER))
    monkeypatch.setattr(settings, "cardimage_timeout_ms", 120_000)
    return s


def _card(
    status: str,
    created_at: datetime,
    owner: uuid.UUID = OWNER,
    *,
    updated_at: datetime | None = None,
    error_code: str = "upstream",
    dog_id: uuid.UUID | None = None,
    card: int | str = 4,
    card_id: uuid.UUID | None = None,
    pick_group: uuid.UUID | None = None,
) -> AiCard:
    # 달 카드는 `month=<정수>`·`card_key="<정수>"`, 종류 카드는 `month=None`·`card_key="strawberry"` —
    # `db/init/38_ai_cards.sql` 의 CHECK `ai_cards_month` 가 요구하는 짝이다 (#593, D-085).
    return AiCard(
        id=card_id or uuid.uuid4(), app_user_id=owner, dog_id=dog_id,
        month=card if isinstance(card, int) else None, card_key=str(card), dog_name="네오",
        title="BLOSSOM 네오", status=status, error_code=error_code if status == "failed" else None,
        pick_group=pick_group,
        created_at=created_at, updated_at=updated_at if updated_at is not None else created_at,
    )


def _usage(used_at: datetime, owner: uuid.UUID = OWNER, *, unfulfilled_attempt: bool = False) -> AiCardUsage:
    return AiCardUsage(
        card_id=uuid.uuid4(), app_user_id=owner, used_at=used_at, unfulfilled_attempt=unfulfilled_attempt
    )


def _check(limit: int = 1, *, dog_id: uuid.UUID | None = None, card: int | str = 4) -> None:
    asyncio.run(quota.check_quota(None, OWNER, now=NOW, daily_limit=limit, dog_id=dog_id, card=card))


def _remaining(limit: int = 1) -> int | None:
    return asyncio.run(quota.daily_remaining(None, OWNER, now=NOW, daily_limit=limit))


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("DAENGS_CARDIMAGE_DAILY_LIMIT", "DAENGS_CARDIMAGE_CONCURRENCY"):
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=None)
    assert s.cardimage_daily_limit == 1
    assert s.cardimage_concurrency == 2


def test_stale_after_covers_worst_case_retry(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    # 엔진·검수 120초씩 × 재시도 2회 = 8분. 그보다 1분 길다.
    monkeypatch.setattr(settings, "cardgen_url", "")
    assert quota.stale_after() == timedelta(minutes=9)


def test_stale_after_covers_gpu_cold_start_when_cardgen_is_configured(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FLUX.2-klein-4B 는 콜드 스타트만 340.7~460초다(#557 E3, #572 12달 실험). 예산이 그보다
    짧으면 정상 진행 중인 카드를 사라진 작업으로 덮는다 — `cardgen_timeout_s` 를 통째로 더한다."""
    monkeypatch.setattr(settings, "cardgen_url", "http://cardgen.example")
    monkeypatch.setattr(settings, "cardgen_timeout_s", 900.0)
    assert quota.stale_after() == timedelta(minutes=9) + timedelta(seconds=900)


def test_stale_after_stays_short_when_cardgen_is_not_configured(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nano Banana 2 만 쓰는 지금 운영에서는 기준이 늘어나면 안 된다 — 죽은 작업이 그만큼 오래 남는다.
    공백뿐인 값도 꺼진 것이다 — `ai_card_engine.default_engine` 이 `strip()` 해서 가르는 것과 같게."""
    monkeypatch.setattr(settings, "cardgen_timeout_s", 900.0)
    for off in ("", "   "):
        monkeypatch.setattr(settings, "cardgen_url", off)
        assert quota.stale_after() == timedelta(milliseconds=4 * settings.cardimage_timeout_ms) + timedelta(seconds=60)


def test_kst_day_start() -> None:
    assert quota.kst_day_start(NOW) == KST_TODAY_START


def test_empty_is_allowed(store: Store) -> None:
    _check()


# ── 동시 1장 ────────────────────────────────────────────────────────────


def test_fresh_generating_is_busy(store: Store) -> None:
    store.ai_cards.append(_card("generating", NOW - timedelta(minutes=1)))
    with pytest.raises(quota.AiCardBusyError):
        _check()


def test_stale_generating_is_expired_not_busy(store: Store) -> None:
    # 정리 기준은 `updated_at` 이다 — 슬롯을 잡을 때마다 그 칸을 찍는다(_claim_slot).
    old = NOW - timedelta(minutes=10)
    card = _card("generating", old, updated_at=old)
    store.ai_cards.append(card)
    _check()
    assert card.status == "failed" and card.error_code == "interrupted"


def test_old_created_at_but_fresh_updated_at_is_busy_not_expired(store: Store) -> None:
    """오래 전에 만들어졌어도 슬롯을 최근에 잡았으면(_claim_slot 이 `updated_at` 을 찍음)
    아직 도는 작업이다 — 정리 기준이 `created_at` 이 아니라 `updated_at` 인 것을 지킨다."""
    card = _card("generating", NOW - timedelta(hours=2), updated_at=NOW - timedelta(minutes=1))
    store.ai_cards.append(card)
    with pytest.raises(quota.AiCardBusyError):
        _check()


# ── 정리 기준은 요청(pick_group) 단위의 마지막 진척부터 ─────────────────


def _group(store: Store, *, leader_status: str, leader_at: datetime, follower_at: datetime) -> tuple[AiCard, AiCard]:
    group = uuid.uuid4()
    leader = _card(leader_status, NOW - timedelta(minutes=30), card_id=group, pick_group=group, updated_at=leader_at)
    follower = _card("generating", NOW - timedelta(minutes=30), pick_group=group, updated_at=follower_at)
    store.ai_cards.extend([leader, follower])
    return leader, follower


def test_follower_waiting_behind_a_slow_leader_is_not_expired(store: Store) -> None:
    """두 번째 카드의 `updated_at` 은 요청 시각이다 — 대기열·첫 카드가 도는 동안 제 예산이 이미 흘렀다.
    첫 카드가 방금 슬롯을 잡아(_claim_slot) 돌고 있으면 그 요청은 살아 있다 — 두 번째를 덮으면
    사용자가 약속받은 카드 하나를 조용히 잃는다."""
    leader, follower = _group(
        store, leader_status="generating", leader_at=NOW - timedelta(minutes=2), follower_at=NOW - timedelta(minutes=12)
    )
    with pytest.raises(quota.AiCardBusyError):
        _check()
    assert leader.status == "generating" and follower.status == "generating"


def test_follower_right_after_leader_finished_is_not_expired(store: Store) -> None:
    """첫 카드가 방금 `ready` 가 됐고 두 번째가 아직 슬롯을 안 잡은 틈 — 끝난 행의 `updated_at` 도 진척이다."""
    _leader, follower = _group(
        store, leader_status="ready", leader_at=NOW - timedelta(seconds=5), follower_at=NOW - timedelta(minutes=12)
    )
    with pytest.raises(quota.AiCardBusyError):
        _check()
    assert follower.status == "generating"


def test_abandoned_group_is_expired_as_a_whole(store: Store) -> None:
    """프로세스가 죽어 그룹 전체의 진척이 멈췄으면 결국 전부 정리된다 — 영영 `generating` 으로 남지 않는다."""
    leader, follower = _group(
        store, leader_status="generating", leader_at=NOW - timedelta(minutes=10), follower_at=NOW - timedelta(minutes=12)
    )
    _check()
    assert (leader.status, leader.error_code) == ("failed", "interrupted")
    assert (follower.status, follower.error_code) == ("failed", "interrupted")


def test_follower_of_a_group_whose_last_progress_is_stale_is_expired(store: Store) -> None:
    """첫 카드는 오래전에 끝났고 두 번째는 슬롯을 끝내 못 잡았다(프로세스가 죽었다) — 정리된다."""
    _leader, follower = _group(
        store, leader_status="ready", leader_at=NOW - timedelta(minutes=10), follower_at=NOW - timedelta(minutes=12)
    )
    _check()
    assert (follower.status, follower.error_code) == ("failed", "interrupted")


def test_other_groups_progress_does_not_keep_a_group_alive(store: Store) -> None:
    """진척은 **같은 pick_group** 안에서만 본다 — 다른 요청이 살아 있다고 죽은 요청이 버티면 안 된다."""
    _alive_leader, alive_follower = _group(
        store, leader_status="ready", leader_at=NOW - timedelta(seconds=5), follower_at=NOW - timedelta(minutes=12)
    )
    dead_leader, dead_follower = _group(
        store, leader_status="generating", leader_at=NOW - timedelta(minutes=10), follower_at=NOW - timedelta(minutes=12)
    )
    with pytest.raises(quota.AiCardBusyError):  # 살아 있는 요청 때문에
        _check()
    assert alive_follower.status == "generating"
    assert dead_leader.status == "failed" and dead_follower.status == "failed"


# ── 하루 한도 — 사용 기록으로 센다 ──────────────────────────────────────


def test_usage_today_hits_limit(store: Store) -> None:
    store.ai_card_usage.append(_usage(KST_TODAY_START))
    with pytest.raises(quota.AiCardLimitError):
        _check()


def test_usage_yesterday_kst_does_not_count(store: Store) -> None:
    store.ai_card_usage.append(_usage(KST_YESTERDAY_LAST))
    _check()


def test_ready_row_without_usage_does_not_count(store: Store) -> None:
    """세는 곳이 `ai_cards` 가 아니라 사용 기록이다 — 기록이 없는 ready 행은 한도에 안 걸린다."""
    store.ai_cards.append(_card("ready", NOW - timedelta(hours=1)))
    _check()


def test_failed_does_not_count(store: Store) -> None:
    store.ai_cards.append(_card("failed", NOW - timedelta(hours=1)))
    _check()


def test_zero_limit_is_unlimited(store: Store) -> None:
    for _ in range(5):
        store.ai_card_usage.append(_usage(NOW - timedelta(hours=1)))
    _check(limit=0)


def test_other_users_rows_are_ignored(store: Store) -> None:
    store.ai_cards.append(_card("generating", NOW, owner=STRANGER))
    store.ai_card_usage.append(_usage(NOW, owner=STRANGER))
    _check()


# ── 돈 나간 시도 상한 — 지울 수 없는 시도 표시만 센다 (#572, D-084) ───────


def _attempt_marks(store: Store, n: int, at: datetime = NOW - timedelta(hours=1)) -> None:
    for _ in range(n):
        store.ai_card_usage.append(_usage(at, unfulfilled_attempt=True))


def test_attempt_mark_does_not_use_the_daily_limit(store: Store) -> None:
    _attempt_marks(store, 1)
    _check()
    assert _remaining() == 1


def test_five_attempt_marks_hit_the_paid_cap(store: Store) -> None:
    _attempt_marks(store, quota.MAX_PAID_FAILURES_PER_DAY)
    with pytest.raises(quota.AiCardLimitError):
        _check()


def test_four_attempt_marks_are_ok(store: Store) -> None:
    _attempt_marks(store, quota.MAX_PAID_FAILURES_PER_DAY - 1)
    _check()


def test_paid_cap_applies_even_when_unlimited(store: Store) -> None:
    _attempt_marks(store, quota.MAX_PAID_FAILURES_PER_DAY)
    with pytest.raises(quota.AiCardLimitError):
        _check(limit=0)


def test_attempt_marks_yesterday_kst_do_not_count(store: Store) -> None:
    _attempt_marks(store, quota.MAX_PAID_FAILURES_PER_DAY, at=KST_YESTERDAY_LAST)
    _check()


def test_failed_card_rows_are_not_the_ledger(store: Store) -> None:
    """실패한 카드 행은 세지 않는다 — 지울 수 있어서 셈이 사라진다. 그 요청의 셈은 슬롯을 잡을 때 남긴
    시도 표시가 맡는다(유료 호출 전에 남으므로 실패 행이 있는 요청에는 표시도 있다)."""
    for code in ("upstream", "no_image", "storage"):
        for _ in range(quota.MAX_PAID_FAILURES_PER_DAY):
            store.ai_cards.append(_card("failed", NOW - timedelta(hours=1), error_code=code))
    _check()


# ── 강아지마다 카드 종류당 한 장 ────────────────────────────────────────


def test_same_dog_same_month_ready_is_taken(store: Store) -> None:
    store.ai_cards.append(_card("ready", NOW - timedelta(days=3), dog_id=DOG, card=4))
    with pytest.raises(quota.AiCardTakenError):
        _check(limit=0, dog_id=DOG, card=4)


def test_month_taken_is_checked_before_daily_limit(store: Store) -> None:
    """둘 다 걸리면 카드별이 먼저다 — 내일 다시 해도 안 되는 이유를 알려 준다."""
    store.ai_cards.append(_card("ready", NOW - timedelta(hours=1), dog_id=DOG, card=4))
    store.ai_card_usage.append(_usage(NOW - timedelta(hours=1)))
    with pytest.raises(quota.AiCardTakenError):
        _check(dog_id=DOG, card=4)


def test_same_dog_same_month_failed_is_not_taken(store: Store) -> None:
    store.ai_cards.append(_card("failed", NOW - timedelta(days=3), dog_id=DOG, card=4))
    _check(limit=0, dog_id=DOG, card=4)


def test_same_dog_other_month_is_ok(store: Store) -> None:
    store.ai_cards.append(_card("ready", NOW - timedelta(days=3), dog_id=DOG, card=4))
    _check(limit=0, dog_id=DOG, card=9)


def test_other_dog_same_month_is_ok(store: Store) -> None:
    store.ai_cards.append(_card("ready", NOW - timedelta(days=3), dog_id=OTHER_DOG, card=4))
    _check(limit=0, dog_id=DOG, card=4)


def test_other_owner_same_dog_same_month_is_ok(store: Store) -> None:
    """보호자마다 따로 센다(A안) — 공동 보호자가 같은 강아지로 만든 카드는 내 달을 막지 않는다."""
    store.ai_cards.append(_card("ready", NOW - timedelta(days=3), owner=STRANGER, dog_id=DOG, card=4))
    _check(limit=0, dog_id=DOG, card=4)


def test_no_dog_id_skips_month_check(store: Store) -> None:
    store.ai_cards.append(_card("ready", NOW - timedelta(days=3), dog_id=None, card=4))
    _check(limit=0, dog_id=None, card=4)


def test_card_args_are_required() -> None:
    """빠뜨려서 카드별 검사가 조용히 꺼지면 안 된다."""
    with pytest.raises(TypeError):
        asyncio.run(quota.check_quota(None, OWNER, now=NOW, daily_limit=1))  # type: ignore[call-arg]


# ── 종류 카드도 같은 규칙 (#593, D-085) ─────────────────────────────────


def test_same_dog_same_kind_ready_is_taken(store: Store) -> None:
    store.ai_cards.append(_card("ready", NOW - timedelta(days=3), dog_id=DOG, card="strawberry"))
    with pytest.raises(quota.AiCardTakenError):
        _check(limit=0, dog_id=DOG, card="strawberry")


def test_a_month_card_does_not_block_a_kind_card(store: Store) -> None:
    """한 강아지가 4월과 딸기를 **동시에** 가질 수 있다 — 세는 칸이 `card_key` 이기 때문이다.
    `month` 로 세던 옛 코드는 종류 카드의 `month` 가 NULL 이라 여기서 틀린 답을 냈다."""
    store.ai_cards.append(_card("ready", NOW - timedelta(days=3), dog_id=DOG, card=4))
    _check(limit=0, dog_id=DOG, card="strawberry")


def test_one_kind_does_not_block_another_kind(store: Store) -> None:
    """딸기가 상추를 막으면 안 된다 — 둘 다 `month` 가 NULL 이라 달로 세면 서로를 막는다."""
    store.ai_cards.append(_card("ready", NOW - timedelta(days=3), dog_id=DOG, card="strawberry"))
    _check(limit=0, dog_id=DOG, card="lettuce")


def test_a_kind_card_does_not_block_a_month_card(store: Store) -> None:
    store.ai_cards.append(_card("ready", NOW - timedelta(days=3), dog_id=DOG, card="strawberry"))
    _check(limit=0, dog_id=DOG, card=4)


def test_daily_limit_is_one_counter_for_months_and_kinds(store: Store) -> None:
    """하루 한도는 카드 종류를 안 본다 — 오늘 4월 카드를 만들었으면 딸기도 못 만든다 (사용자 결정 09-18)."""
    store.ai_card_usage.append(_usage(KST_TODAY_START))
    with pytest.raises(quota.AiCardLimitError):
        _check(limit=1, dog_id=DOG, card="strawberry")


# ── 남은 횟수 ───────────────────────────────────────────────────────────


def test_remaining_is_none_when_unlimited(store: Store) -> None:
    assert _remaining(limit=0) is None


def test_remaining_counts_down_and_clamps_at_zero(store: Store) -> None:
    assert _remaining() == 1
    store.ai_card_usage.append(_usage(NOW - timedelta(hours=1)))
    assert _remaining() == 0
    store.ai_card_usage.append(_usage(NOW - timedelta(minutes=30)))
    assert _remaining() == 0
    store.ai_card_usage.append(_usage(KST_YESTERDAY_LAST))
    assert _remaining(limit=3) == 1
