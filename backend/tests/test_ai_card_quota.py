"""`services/ai_card_quota.py` — 앱 사용자 AI 카드 생성 한도 (#537 · #543, D-076 · D-077).

제품 규칙(사용자 결정 2026-09-15): 동시 1장 · KST 하루 N회(**사용 기록**으로 셈 — 지워도 안 돌아옴,
실패는 안 셈) · 강아지마다 달마다 한 장(보호자마다 따로) · 돈 나간 실패 하루 5번.
#572(D-084): 닮음 미달 뽑기는 하루 한도 대신 그 5번에 든다 · 정리 기준은 요청 단위 마지막 진척부터 ·
GPU 경로면 콜드 스타트를 예산에 더한다.
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
    month: int = 4,
    card_id: uuid.UUID | None = None,
    pick_group: uuid.UUID | None = None,
) -> AiCard:
    return AiCard(
        id=card_id or uuid.uuid4(), app_user_id=owner, dog_id=dog_id, month=month, dog_name="네오",
        title="BLOSSOM 네오", status=status, error_code=error_code if status == "failed" else None,
        pick_group=pick_group,
        created_at=created_at, updated_at=updated_at if updated_at is not None else created_at,
    )


def _usage(used_at: datetime, owner: uuid.UUID = OWNER, *, below_judge_min: bool = False) -> AiCardUsage:
    return AiCardUsage(card_id=uuid.uuid4(), app_user_id=owner, used_at=used_at, below_judge_min=below_judge_min)


def _check(limit: int = 1, *, dog_id: uuid.UUID | None = None, month: int = 4) -> None:
    asyncio.run(quota.check_quota(None, OWNER, now=NOW, daily_limit=limit, dog_id=dog_id, month=month))


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
    """FLUX.2-klein-4B 는 콜드 스타트만 6~7분이다(#557 E3, #572 12달 실험 460초). 예산이 그보다
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


# ── 돈 나간 실패 — 그대로 ───────────────────────────────────────────────


def _failures(store: Store, n: int, code: str = "upstream", at: datetime = NOW - timedelta(hours=1)) -> None:
    for _ in range(n):
        store.ai_cards.append(_card("failed", at, error_code=code))


def test_five_paid_failures_today_hit_limit(store: Store) -> None:
    _failures(store, quota.MAX_PAID_FAILURES_PER_DAY)
    with pytest.raises(quota.AiCardLimitError):
        _check()


def test_four_paid_failures_today_are_ok(store: Store) -> None:
    _failures(store, quota.MAX_PAID_FAILURES_PER_DAY - 1)
    _check()


def test_interrupted_failures_do_not_count(store: Store) -> None:
    _failures(store, quota.MAX_PAID_FAILURES_PER_DAY, code="interrupted")
    _check()


def test_paid_failures_yesterday_kst_do_not_count(store: Store) -> None:
    _failures(store, quota.MAX_PAID_FAILURES_PER_DAY, at=KST_YESTERDAY_LAST)
    _check()


def test_paid_failure_cap_applies_even_when_unlimited(store: Store) -> None:
    _failures(store, quota.MAX_PAID_FAILURES_PER_DAY, code="no_image")
    with pytest.raises(quota.AiCardLimitError):
        _check(limit=0)


# ── 닮음 미달 뽑기 — 하루 한도는 안 쓰고, 돈 나간 헛시도 상한에 든다 (#572) ──


def _below_min_groups(store: Store, n: int, at: datetime = NOW - timedelta(hours=1)) -> None:
    for _ in range(n):
        store.ai_card_usage.append(_usage(at, below_judge_min=True))


def test_below_min_mark_does_not_use_the_daily_limit(store: Store) -> None:
    _below_min_groups(store, 1)
    _check()
    assert _remaining() == 1


def test_five_below_min_groups_hit_the_paid_cap(store: Store) -> None:
    """사진이 늘 기준 미만으로 나오면 하루 한도가 안 줄어 GPU·검수를 끝없이 부를 수 있다 —
    `count_failed_since` 는 `failed` 행만 세고 닮음 미달 카드는 `ready` 라 거기 안 걸린다."""
    _below_min_groups(store, quota.MAX_PAID_FAILURES_PER_DAY)
    with pytest.raises(quota.AiCardLimitError):
        _check(limit=0)


def test_four_below_min_groups_are_ok(store: Store) -> None:
    _below_min_groups(store, quota.MAX_PAID_FAILURES_PER_DAY - 1)
    _check()


def test_below_min_groups_and_paid_failures_share_one_cap(store: Store) -> None:
    _below_min_groups(store, 3)
    _failures(store, quota.MAX_PAID_FAILURES_PER_DAY - 3)
    with pytest.raises(quota.AiCardLimitError):
        _check()


def test_below_min_marks_yesterday_kst_do_not_count(store: Store) -> None:
    _below_min_groups(store, quota.MAX_PAID_FAILURES_PER_DAY, at=KST_YESTERDAY_LAST)
    _check()


# ── 강아지마다 달마다 한 장 ─────────────────────────────────────────────


def test_same_dog_same_month_ready_is_taken(store: Store) -> None:
    store.ai_cards.append(_card("ready", NOW - timedelta(days=3), dog_id=DOG, month=4))
    with pytest.raises(quota.AiCardMonthTakenError):
        _check(limit=0, dog_id=DOG, month=4)


def test_month_taken_is_checked_before_daily_limit(store: Store) -> None:
    """둘 다 걸리면 달별이 먼저다 — 내일 다시 해도 안 되는 이유를 알려 준다."""
    store.ai_cards.append(_card("ready", NOW - timedelta(hours=1), dog_id=DOG, month=4))
    store.ai_card_usage.append(_usage(NOW - timedelta(hours=1)))
    with pytest.raises(quota.AiCardMonthTakenError):
        _check(dog_id=DOG, month=4)


def test_same_dog_same_month_failed_is_not_taken(store: Store) -> None:
    store.ai_cards.append(_card("failed", NOW - timedelta(days=3), dog_id=DOG, month=4))
    _check(limit=0, dog_id=DOG, month=4)


def test_same_dog_other_month_is_ok(store: Store) -> None:
    store.ai_cards.append(_card("ready", NOW - timedelta(days=3), dog_id=DOG, month=4))
    _check(limit=0, dog_id=DOG, month=9)


def test_other_dog_same_month_is_ok(store: Store) -> None:
    store.ai_cards.append(_card("ready", NOW - timedelta(days=3), dog_id=OTHER_DOG, month=4))
    _check(limit=0, dog_id=DOG, month=4)


def test_other_owner_same_dog_same_month_is_ok(store: Store) -> None:
    """보호자마다 따로 센다(A안) — 공동 보호자가 같은 강아지로 만든 카드는 내 달을 막지 않는다."""
    store.ai_cards.append(_card("ready", NOW - timedelta(days=3), owner=STRANGER, dog_id=DOG, month=4))
    _check(limit=0, dog_id=DOG, month=4)


def test_no_dog_id_skips_month_check(store: Store) -> None:
    store.ai_cards.append(_card("ready", NOW - timedelta(days=3), dog_id=None, month=4))
    _check(limit=0, dog_id=None, month=4)


def test_month_args_are_required() -> None:
    """빠뜨려서 달별 검사가 조용히 꺼지면 안 된다."""
    with pytest.raises(TypeError):
        asyncio.run(quota.check_quota(None, OWNER, now=NOW, daily_limit=1))  # type: ignore[call-arg]


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
