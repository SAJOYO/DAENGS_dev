"""상태 페이지의 보행 항목 — `gait` 큐를 듣는 워커가 있는가 (D-063 4단계).

옛 `gait-analysis` 컨테이너의 `/healthz` 를 두들기던 자리를 Celery 브로커 조회로 바꿨습니다.
`crawl_workers` 의 celery 갈래와 같은 모양이라 지키는 것도 같습니다:

  · 브로커 주소가 없으면 `BrokerUnavailable` → 상태는 `absent` (고장이 아니라 "이 환경엔 없다")
  · **브로커 조회 자체가 예외를 던져도** `BrokerUnavailable` → `absent` — 운영 status 의 실패 경계
  · 다른 큐만 답하면 빈 목록 → `absent` (같은 브로커에 크롤러·실시간 워커가 붙어 있다)
  · `gait` 큐가 답하면 그 워커 이름 → `ok`
  · 큐 이름은 워커가 실제로 듣는 큐(`tasks/gait.py`)와 같아야 한다 — 어긋나면 워커가 떠 있어도
    화면은 영영 `absent` 다

브로커에 실제로 붙지 않습니다 — `celery.Celery` 를 대역으로 갈아 끼웁니다.
"""

from __future__ import annotations

import pytest

from daengs_backend.config import settings
from daengs_backend.schemas.status import StatusState
from daengs_backend.services import gait as gait_service
from daengs_backend.services import status as status_service


def _fake_celery(monkeypatch, *, replies=None, raise_exc: Exception | None = None):
    """`Celery(broker=…).control.inspect(timeout=…).active_queues()` 를 흉내 냅니다.

    실제 호출이 브로커에 붙지 않게 `celery.Celery` 를 통째로 대역으로 바꿉니다.
    """
    seen: dict = {}

    class _Inspect:
        def __init__(self, timeout):
            seen["timeout"] = timeout

        def active_queues(self):
            if raise_exc is not None:
                raise raise_exc
            return replies

    class _Control:
        def inspect(self, timeout=None):
            return _Inspect(timeout)

    class _App:
        def __init__(self, broker=None):
            seen["broker"] = broker
            self.control = _Control()

    import celery

    monkeypatch.setattr(celery, "Celery", _App)
    monkeypatch.setattr(settings, "redis_url", "redis://fake:6379/0")
    return seen


# ── gait_workers() ────────────────────────────────────────────────────────────
def test_broker_address_missing_is_broker_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "")
    with pytest.raises(gait_service.BrokerUnavailable):
        gait_service.gait_workers()


def test_broker_query_exception_becomes_broker_unavailable(monkeypatch):
    """⚠️ 브로커가 죽어서 조회가 터지는 것은 500 이 아닙니다 — 상태 항목 하나가 화면을 막으면 안 됩니다."""
    _fake_celery(monkeypatch, raise_exc=OSError("connection refused"))
    with pytest.raises(gait_service.BrokerUnavailable, match="OSError"):
        gait_service.gait_workers()


def test_no_reply_is_an_empty_list(monkeypatch):
    _fake_celery(monkeypatch, replies=None)  # 아무도 안 답하면 None 이지 {} 가 아니다
    assert gait_service.gait_workers() == []


def test_other_queues_do_not_count(monkeypatch):
    """같은 브로커의 크롤러·실시간 워커는 보행 워커가 아닙니다 — `ping()` 을 안 쓰는 이유."""
    _fake_celery(
        monkeypatch,
        replies={
            "celery@crawler": [{"name": "crawl"}],
            "celery@realtime": [{"name": "celery"}],
        },
    )
    assert gait_service.gait_workers() == []


def test_gait_queue_listeners_are_returned_sorted(monkeypatch):
    seen = _fake_celery(
        monkeypatch,
        replies={
            "celery@gait-b": [{"name": "gait"}],
            "celery@crawler": [{"name": "crawl"}],
            "celery@gait-a": [{"name": "gait"}, {"name": "celery"}],
        },
    )
    assert gait_service.gait_workers(timeout_sec=1.5) == ["celery@gait-a", "celery@gait-b"]
    assert seen["timeout"] == 1.5
    assert seen["broker"] == "redis://fake:6379/0"


def test_queue_name_matches_the_worker_definition():
    """워커가 듣는 큐와 상태 페이지가 찾는 큐가 어긋나면 화면이 영영 `absent` 입니다."""
    from daengs_backend.tasks import gait as gait_tasks

    assert gait_service.GAIT_QUEUE == gait_tasks.app.conf.task_default_queue == "gait"


# ── status._gait() 매핑 ───────────────────────────────────────────────────────
async def test_status_is_absent_without_broker(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "")
    state, detail = await status_service._gait()
    assert state is StatusState.ABSENT
    assert "브로커" in detail


async def test_status_is_absent_when_broker_query_raises(monkeypatch):
    _fake_celery(monkeypatch, raise_exc=OSError("connection refused"))
    state, _ = await status_service._gait()
    assert state is StatusState.ABSENT


async def test_status_is_absent_when_no_gait_worker(monkeypatch):
    _fake_celery(monkeypatch, replies={"celery@crawler": [{"name": "crawl"}]})
    state, detail = await status_service._gait()
    assert state is StatusState.ABSENT
    assert "떠 있지 않습니다" in detail


async def test_status_is_ok_with_gait_worker(monkeypatch):
    _fake_celery(monkeypatch, replies={"celery@64693ce400a7": [{"name": "gait"}]})
    state, detail = await status_service._gait()
    assert state is StatusState.OK
    assert "gait-worker 1대" in detail and "celery@64693ce400a7" in detail
