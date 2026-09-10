"""`running` 을 시간으로 가르는 판정 (#393 · RAG-085 ③).

**고친 것은 쓰는 쪽이 아니라 읽는 쪽이다.** `crawl_runs` 에 `running` 이 남는 것은 버그가
아니라 신호다 — `daengs_life/tasks/crawl_runs.py` 의 `start()` 가 *"워커가 중간에 죽으면 이
행이 `running` 으로 남는데, 그것이 정보다"* 라고 적어 뒀고, 기록이 크롤을 죽이지 않는다는
계약 때문에 `finish()` 를 못 부르고 죽는 경로가 **항상 열려 있다.** 그래서 잔존 행은 앞으로도
계속 생기고, **읽는 쪽이 시간을 봐야** 한다.

여기서 붙잡는 불변식 둘:

1. **도는 중인 것만 있으면 `OK`** 다. 예전에는 `running` 이 1건이라도 있으면 DEGRADED 였고,
   그래서 몇 달 전 잔존 행 하나가 화면을 영영 노랗게 잡았다 — 진짜 문제가 생겨도 이미 노랑이라
   안 보였다. **그것이 이 카드가 고친 것**이라 여기서 깨지면 원래 병으로 돌아간 것이다.
2. **자르는 시각은 services 가 계산한다.** `repositories/crawl_run.py` 는 쿼리만 있고 판단이
   없어서(파일 머리), `RUNNING_STALE_AFTER` 를 저쪽으로 내리면 계층이 무너진다 (D-011).

DB 는 안 건드린다 — 팀에 DB 가 하나뿐이라 (`test_status_api.py` 와 같은 규칙).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from daengs_backend.schemas.status import StatusState
from daengs_backend.services import crawl as crawl_service
from daengs_backend.services import status as status_service

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

SESSION = object()


def _run(status: str = "ok", *, hours_ago: float = 1.0) -> SimpleNamespace:
    return SimpleNamespace(source_id="src", status=status,
                           started_at=datetime.now(UTC) - timedelta(hours=hours_ago))


@pytest.fixture
def crawl(monkeypatch: pytest.MonkeyPatch):
    """`_crawl` 이 바깥으로 나가는 길을 전부 막고, 안 끝난 실행 수만 주입한다."""
    def _setup(*, active: int, stale: int, runs=None) -> None:
        async def _latest(_session):
            return runs if runs is not None else [_run()]

        async def _split(_session):
            return active, stale

        monkeypatch.setattr(crawl_service, "latest", _latest)
        monkeypatch.setattr(crawl_service, "running_split", _split)
        monkeypatch.setattr(crawl_service, "crawl_workers", lambda _t: ["worker@host"])
        monkeypatch.setattr(status_service.settings, "crawl_backend", "celery")

    return _setup


# ---------------------------------------------------------------- 판정


async def _judge(session=SESSION) -> tuple[StatusState, str]:
    return await status_service._crawl(session)


@pytest.mark.asyncio
async def test_도는_중인_것만_있으면_ok(crawl) -> None:
    """🔴 **이 카드의 본체다.** 예전에는 여기가 DEGRADED 였고 그래서 지표가 죽었다."""
    crawl(active=2, stale=0)
    state, detail = await _judge()
    assert state is StatusState.OK
    assert "지금 도는 중 2건" in detail
    assert "죽어 남은" not in detail


@pytest.mark.asyncio
async def test_오래된_running_은_degraded_이고_이유를_말한다(crawl) -> None:
    crawl(active=0, stale=1)
    state, detail = await _judge()
    assert state is StatusState.DEGRADED
    assert "워커가 죽어 남은 실행 1건" in detail
    # 몇 시간 넘으면 그렇게 보는지가 문구에 있어야 한다 — 없으면 사람이 임계값을 못 안다
    assert "6시간" in detail


@pytest.mark.asyncio
async def test_둘_다_있으면_죽은_쪽을_먼저_말한다(crawl) -> None:
    """사람이 **할 일이 있는 쪽**이 이긴다. 도는 중인 것은 기다리면 되지만 잔존 행은 봐야 한다."""
    crawl(active=3, stale=1)
    state, detail = await _judge()
    assert state is StatusState.DEGRADED
    assert "죽어 남은 실행 1건" in detail
    assert "지금 도는 중" not in detail


@pytest.mark.asyncio
async def test_안_끝난_것이_없으면_그대로_ok(crawl) -> None:
    crawl(active=0, stale=0)
    state, detail = await _judge()
    assert state is StatusState.OK
    assert "도는 중" not in detail and "죽어 남은" not in detail


@pytest.mark.asyncio
async def test_failed_가_stale_보다_먼저다(crawl) -> None:
    """순서는 안 바꿨다 — `failed`·`unavailable` 이 더 급하다."""
    crawl(active=0, stale=5, runs=[_run("failed"), _run("unavailable")])
    state, detail = await _judge()
    assert state is StatusState.DEGRADED
    assert "마지막 실행이 어긋난 소스" in detail
    assert "죽어 남은" not in detail


# ---------------------------------------------------------------- 계층과 임계값


@pytest.mark.asyncio
async def test_running_split_은_자르는_시각을_계산해_넘긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """**임계값은 services 소유다** — repositories 는 받은 시각으로 세기만 한다 (D-011).

    저쪽이 `RUNNING_STALE_AFTER` 를 알게 되면 "얼마나 오래면 죽은 것인가"라는 판단이
    쿼리 계층으로 내려가고, 그러면 이 값을 바꿀 자리가 둘이 된다.
    """
    seen: list[datetime | None] = []

    async def _count(_session, *, started_before: datetime | None = None) -> int:
        seen.append(started_before)
        return 2 if started_before is not None else 7

    monkeypatch.setattr(crawl_service.repo, "count_running", _count)
    before = datetime.now(UTC)
    active, stale = await crawl_service.running_split(SESSION)
    after = datetime.now(UTC)

    assert (active, stale) == (5, 2), "도는 중 = 전체 − 오래된 것"
    cutoff = next(s for s in seen if s is not None)
    assert before - crawl_service.RUNNING_STALE_AFTER <= cutoff <= after - crawl_service.RUNNING_STALE_AFTER
    assert None in seen, "전체도 한 번은 세야 한다 (running_count 와 합이 맞아야 하므로)"


def test_임계값이_크롤_주기보다_짧다() -> None:
    """**위 벽**: Beat 가 하루 한 번(KST 04:00) 돈다 (RAG-050).

    24시간을 넘기면 "어제 죽어 남은 행"과 "오늘 도는 행"이 겹쳐 **애초에 못 가른다.**
    아래 벽(소스 하나의 수집은 길어야 분 단위)은 코드로 잡을 수 없어 주석에 있다.
    """
    assert timedelta(0) < crawl_service.RUNNING_STALE_AFTER < timedelta(days=1)
