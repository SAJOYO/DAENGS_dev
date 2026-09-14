"""`services/ai_card_quota.py` — 앱 사용자 AI 카드 생성 한도 (#537, D-076).

지금 규칙은 테스트 단계용이다: 사용자별 동시 1장 + KST 하루 `ready` N장. 제품 규칙이 정해지면
`check_quota` 하나를 통째로 바꾼다 — 이 파일도 같이 바뀐다.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeAdmin, FakeAppUser, Store, install

from daengs_backend.config import Settings, settings
from daengs_backend.models import AiCard
from daengs_backend.services import ai_card_quota as quota

OWNER = uuid.uuid4()
STRANGER = uuid.uuid4()
NOW = datetime(2026, 9, 14, 3, 0, tzinfo=UTC)  # KST 12:00


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    s = install(Store(FakeAdmin()), monkeypatch)
    s.add_app_user(FakeAppUser(kakao_id=1, id=OWNER))
    monkeypatch.setattr(settings, "cardimage_timeout_ms", 120_000)
    return s


def _card(status: str, created_at: datetime, owner: uuid.UUID = OWNER) -> AiCard:
    return AiCard(
        id=uuid.uuid4(), app_user_id=owner, month=4, dog_name="네오", title="BLOSSOM 네오",
        status=status, error_code="upstream" if status == "failed" else None,
        created_at=created_at, updated_at=created_at,
    )


def _check(limit: int = 1) -> None:
    asyncio.run(quota.check_quota(None, OWNER, now=NOW, daily_limit=limit))


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("DAENGS_CARDIMAGE_DAILY_LIMIT", "DAENGS_CARDIMAGE_CONCURRENCY"):
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=None)
    assert s.cardimage_daily_limit == 1
    assert s.cardimage_concurrency == 2


def test_stale_after_covers_worst_case_retry(store: Store) -> None:
    # 엔진·검수 120초씩 × 재시도 2회 = 8분. 그보다 1분 길다.
    assert quota.stale_after() == timedelta(minutes=9)


def test_kst_day_start() -> None:
    assert quota.kst_day_start(NOW) == datetime(2026, 9, 13, 15, 0, tzinfo=UTC)


def test_empty_is_allowed(store: Store) -> None:
    _check()


def test_fresh_generating_is_busy(store: Store) -> None:
    store.ai_cards.append(_card("generating", NOW - timedelta(minutes=1)))
    with pytest.raises(quota.AiCardBusyError):
        _check()


def test_stale_generating_is_expired_not_busy(store: Store) -> None:
    card = _card("generating", NOW - timedelta(minutes=10))
    store.ai_cards.append(card)
    _check()
    assert card.status == "failed" and card.error_code == "interrupted"


def test_ready_today_hits_limit(store: Store) -> None:
    store.ai_cards.append(_card("ready", datetime(2026, 9, 13, 15, 0, tzinfo=UTC)))  # KST 14일 00:00
    with pytest.raises(quota.AiCardLimitError):
        _check()


def test_ready_yesterday_kst_does_not_count(store: Store) -> None:
    store.ai_cards.append(_card("ready", datetime(2026, 9, 13, 14, 59, tzinfo=UTC)))  # KST 13일 23:59
    _check()


def test_failed_does_not_count(store: Store) -> None:
    store.ai_cards.append(_card("failed", NOW - timedelta(hours=1)))
    _check()


def test_zero_limit_is_unlimited(store: Store) -> None:
    for _ in range(5):
        store.ai_cards.append(_card("ready", NOW - timedelta(hours=1)))
    _check(limit=0)


def test_other_users_rows_are_ignored(store: Store) -> None:
    store.ai_cards.append(_card("generating", NOW, owner=STRANGER))
    store.ai_cards.append(_card("ready", NOW, owner=STRANGER))
    _check()
