"""상태 페이지가 보는 신호들을 모읍니다 (#180 · 로드맵 B1).

**한 항목이 죽어도 나머지는 답합니다.** 항목마다 짧은 timeout 을 걸고 예외를 그 항목의
상태로 바꿉니다 — 그래서 이 서비스는 예외를 밖으로 던지지 않고, 라우터는 언제나 200 입니다.
"서비스가 살아 있나"를 묻는 화면이 그 질문 때문에 500 을 받으면 아무 말도 못 합니다.

**DB 를 쓰는 항목은 순서대로 돕니다.** 요청 하나가 세션 하나를 쓰는데 `AsyncSession` 은
동시 사용을 못 견딥니다 (같은 커넥션에 두 쿼리가 겹치면 asyncpg 가 깨집니다). 나머지
항목은 서로 무관하므로 같이 돕니다.

**`daengs_life` 를 import 하지 않습니다.** 접점은 `main.py` 와
`orchestration/adapters/life.py` 둘뿐이고 `tests/test_main_stays_light.py` 가 그것을
기계로 지킵니다 (D-035). 그래서 예열 상태는 `app.state` 에서 받고(`core/warm_up.py`),
Redis 일 예산은 `daengs_life.realtime.cache` 를 부르지 않고 **키 이름을 알고 직접**
읽습니다 — `config.py` 의 `redis_url` 주석이 크롤 태스크를 이름 문자열로 던지며 한 것과
같은 판단입니다. 대가는 이름이 두 군데가 되는 것이고, `tests/test_status_api.py` 가
키 모양을 고정해 그 어긋남을 잡습니다.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.config import settings
from daengs_backend.core.warm_up import WarmUp, WarmUpPhase
from daengs_backend.schemas.status import StatusState
from daengs_backend.services import crawl as crawl_service

log = logging.getLogger(__name__)

#: 항목 하나에 주는 시간. 화면이 30초마다 폴링하므로 넉넉할 이유가 없습니다 —
#: 죽은 것을 "죽었다"고 빨리 말하는 편이 낫습니다.
ITEM_TIMEOUT_SEC = 2.0

#: 크롤러 워커에게 묻는 시간. `ITEM_TIMEOUT_SEC` 안에 끝나야 하므로 더 짧습니다.
WORKER_PING_SEC = 1.0

#: 이름을 푸는 데 주는 시간. compose 안이면 도커 DNS 가 밀리초로 답하므로, 이것을 못
#: 지키는 것은 "그 이름이 이 네트워크에 없다" 는 뜻입니다 (`_probe` 주석).
DNS_TIMEOUT_SEC = 0.5

#: data.go.kr 일 예산 카운터의 Redis 키는 `rt:budget:{group}:{day}` 입니다
#: (`daengs_life/realtime/cache.py` 의 `PREFIX` · `_budget_key`).
BUDGET_KEY_PREFIX = "rt"

#: 그룹과 한도. `daengs_life/realtime/cache.yaml` 의 `budgets` 를 따라 적은 것입니다.
#: **한도가 `null` 인 것(apihub · kakao)은 뺐습니다** — 셀 수는 있어도 "얼마나 남았나"를
#: 말할 수 없어서, 화면에 숫자만 띄우면 소진 직전과 구분이 안 됩니다.
DAILY_BUDGETS: dict[str, int] = {
    "datagokr-vilage-fcst": 1000,
    "datagokr-airkorea": 1000,
    "datagokr-warning": 1000,
    "datagokr-stations": 1000,
}

#: 예산을 KST 날짜로 셉니다 — data.go.kr 의 일 한도가 그 축입니다
#: (`daengs_life/realtime/cache.py` 의 `_day`).
KST = timezone(timedelta(hours=9))

#: 마지막 수집이 이보다 오래되면 `degraded`. Beat 가 매일 04:00 에 due 소스만 받으므로
#: 하루로 잡으면 "오늘 due 가 아닌 소스" 가 늘 노랗습니다.
CRAWL_STALE_AFTER = timedelta(days=3)


@dataclass(frozen=True)
class StatusItem:
    name: str
    label: str
    state: StatusState
    detail: str


# ---------------------------------------------------------------- 조립
def checked_at() -> datetime:
    """이 응답이 언제 본 값인가. 테스트가 갈아끼울 수 있게 이름으로 둡니다."""
    return datetime.now(UTC)


async def collect(session: AsyncSession, warm_up: WarmUp | None) -> list[StatusItem]:
    """항목을 전부 모읍니다. **예외를 던지지 않습니다.**

    순서는 화면에 나오는 순서입니다 — 사람이 먼저 볼 것(DB · 모델)을 앞에 둡니다.
    """
    db_items, others = await asyncio.gather(
        _db_chain(session),
        asyncio.gather(
            _guard("screening", "피부 스크리닝 가중치", _screening()),
            _guard("redis", "Redis · 일 예산", _redis()),
            _guard("place", "장소 검색", _place()),
            _guard("journey", "경로 스냅샷", _journey()),
            _guard("gait", "보행 분석", _gait()),
        ),
    )
    db, crawl = db_items
    screening, redis_, place, journey, gait = others
    return [db, _warm_up_item(warm_up), screening, redis_, place, journey, crawl, gait]


async def _db_chain(session: AsyncSession) -> tuple[StatusItem, StatusItem]:
    """DB 를 쓰는 두 항목. **순서대로** 돕니다 (세션 하나라서).

    DB 가 죽었으면 크롤은 물어보지 않습니다 — 같은 이유로 실패할 것이고, 화면에 같은
    에러가 두 줄 뜨면 무엇이 원인인지 흐려집니다.
    """
    db = await _guard("db", "데이터베이스", _db(session))
    if db.state is StatusState.DOWN:
        crawl = StatusItem("crawl", "마지막 크롤", StatusState.DOWN,
                           "DB 에 닿지 못해 확인하지 못했습니다.")
    else:
        crawl = await _guard("crawl", "마지막 크롤", _crawl(session))
    return db, crawl


async def _guard(name: str, label: str, work: Awaitable[tuple[StatusState, str]]) -> StatusItem:
    """한 항목을 시간과 예외로부터 격리합니다.

    **예외를 `down` 으로 바꿉니다.** 상태 화면에서 "확인하다 터졌다"와 "죽었다"는 사람이
    할 일이 같습니다 — 가서 보는 것입니다. 대신 예외 종류를 `detail` 에 남겨 어느 쪽인지
    알 수 있게 합니다.
    """
    try:
        async with asyncio.timeout(ITEM_TIMEOUT_SEC):
            state, detail = await work
    except TimeoutError:
        return StatusItem(name, label, StatusState.DOWN,
                          f"{ITEM_TIMEOUT_SEC:.0f}초 안에 답하지 않았습니다.")
    except Exception as e:                      # noqa: BLE001 — 항목 하나가 화면을 막으면 안 된다
        log.warning("상태 확인 실패 — %s: %s", name, e)
        return StatusItem(name, label, StatusState.DOWN, f"확인하지 못했습니다 — {type(e).__name__}: {e}")
    return StatusItem(name, label, state, detail)


# ---------------------------------------------------------------- 항목들
async def _db(session: AsyncSession) -> tuple[StatusState, str]:
    """`/health` 와 **같은 쿼리**입니다 (`routers/health.py`). 다른 것을 물으면 두 화면이
    서로 다른 답을 하는 날이 옵니다."""
    await session.execute(text("SELECT 1"))
    return StatusState.OK, f"{settings.db_host}:{settings.db_port}/{settings.db_name} 에 닿습니다."


def _warm_up_item(warm_up: WarmUp | None) -> StatusItem:
    """임베딩 예열. **`app.state` 만 읽습니다** (`core/warm_up.py` 가 이유를 갖고 있습니다)."""
    name, label = "warm-up", "임베딩 모델 (/ask)"
    if warm_up is None:
        return StatusItem(name, label, StatusState.DEGRADED,
                          "예열 기록이 없습니다 — lifespan 을 거치지 않고 뜬 프로세스입니다.")

    took = warm_up.elapsed_sec
    if warm_up.phase is WarmUpPhase.READY:
        return StatusItem(name, label, StatusState.OK,
                          f"올라와 있습니다 ({took:.1f}초 걸렸습니다)." if took is not None
                          else "올라와 있습니다.")
    if warm_up.phase is WarmUpPhase.LOADING:
        return StatusItem(name, label, StatusState.DEGRADED,
                          f"올리는 중입니다 ({took:.0f}초째). 그동안 `/ask` 는 503 입니다."
                          if took is not None else "올리는 중입니다. 그동안 `/ask` 는 503 입니다.")
    if warm_up.phase is WarmUpPhase.DISABLED:
        return StatusItem(name, label, StatusState.ABSENT,
                          "예열을 끄고 떴습니다 (DAENGS_WARM_UP_ENCODER=false). "
                          "첫 `/ask` 요청이 로드를 뭅니다 — 설계입니다.")
    return StatusItem(name, label, StatusState.DOWN,
                      "못 올렸습니다 — `/ask` 만 503 이고 다른 API 는 멀쩡합니다. "
                      "대개 `ml` 그룹이 없는 것입니다 (`uv sync --group ml`).")


async def _screening() -> tuple[StatusState, str]:
    """피부 가중치. **같은 프로세스 안 라우터라 HTTP 로 자기를 부르지 않습니다.**

    `daengs_screening.service.healthz()` 를 직접 부릅니다 — 그쪽은 D-040 으로 이 저장소에
    들어왔지만 스크리닝 파트 소유라 **읽기만** 합니다. 그 함수가 이미 "모델을 올리지
    않는다" 를 지키고 있어서(가중치 350MB), 여기서 더 할 일이 없습니다.
    """
    from daengs_screening.service import healthz

    body = await asyncio.to_thread(healthz)
    where = body.get("release_dir")
    if not body.get("loaded"):
        # 고장이 아닙니다 — 첫 요청이 올리는 것이 설계입니다. 다만 "가중치가 서버에 있나"는
        # 아직 아무도 확인하지 않은 상태라 `ok` 라고 말할 수도 없습니다.
        return StatusState.DEGRADED, f"아직 안 올렸습니다 — 첫 요청이 올립니다 (설계). release_dir={where}"
    return StatusState.OK, f"올라와 있습니다. threshold={body.get('threshold')} release_dir={where}"


async def _redis() -> tuple[StatusState, str]:
    """Redis ping + data.go.kr 일 예산 카운터.

    **일 예산이 Redis 에 있는 이유**가 곧 이 항목이 있는 이유입니다 — 개발 PC 와 서버가
    같은 카운터를 봐야 1,000회/일 을 하나로 셉니다 (D-019). Redis 가 죽으면 앱은 그대로
    뜨고 **카운터만 조용히 안 쌓입니다.** 그것을 볼 자리가 여기입니다.
    """
    if not settings.redis_url:
        return StatusState.ABSENT, "REDIS_URL 이 없습니다 — 이 프로세스는 Redis 를 안 씁니다."

    used = await asyncio.to_thread(_redis_budget_sync)
    spent = [f"{group} {used[group]}/{limit}" for group, limit in DAILY_BUDGETS.items()]
    exhausted = [g for g, limit in DAILY_BUDGETS.items() if used[g] >= limit]
    if exhausted:
        return StatusState.DEGRADED, f"일 예산 소진: {', '.join(exhausted)}. " + " · ".join(spent)
    return StatusState.OK, "붙어 있습니다. 오늘 쓴 양 — " + " · ".join(spent)


def _redis_budget_sync() -> dict[str, int]:
    """동기 Redis. **지연 import 입니다** — 이 모듈이 Redis 없이도 읽혀야 합니다
    (`daengs_life/realtime/cache.py` 와 같은 이유)."""
    import redis

    client = redis.Redis.from_url(
        settings.redis_url, socket_connect_timeout=1.0, socket_timeout=1.0
    )
    try:
        client.ping()
        day = datetime.now(KST).strftime("%Y%m%d")
        raw = client.mget([f"{BUDGET_KEY_PREFIX}:budget:{g}:{day}" for g in DAILY_BUDGETS])
    finally:
        client.close()
    return {g: int(v) if v else 0 for g, v in zip(DAILY_BUDGETS, raw, strict=True)}


async def _place() -> tuple[StatusState, str]:
    return await _probe(settings.place_service_url, ["/health", "/health/ready"])


async def _journey() -> tuple[StatusState, str]:
    return await _probe(settings.journey_service_url, ["/health"])


async def _gait() -> tuple[StatusState, str]:
    """gait 는 compose 에서 `profile: gait` 라 **기본으로는 안 뜹니다** (D-038).
    그래서 `absent` 가 정상이고, 그것이 이 화면에서 `down` 과 갈려야 하는 이유입니다."""
    return await _probe(settings.gait_service_url, ["/healthz"])


async def _crawl(session: AsyncSession) -> tuple[StatusState, str]:
    """마지막 크롤. **워커가 없는 환경은 `absent`** 입니다.

    행이 있다는 것과 크롤러가 있다는 것은 다릅니다 — GCP 는 09-02 로컬 덤프를 쓰고 있어서
    행은 있는데 워커가 없습니다 (`services/crawl.py` 의 `crawl_workers` 주석).
    """
    runs = await crawl_service.latest(session)
    last = max((r.started_at for r in runs), default=None)
    when = f"마지막 수집 {last:%Y-%m-%d %H:%M}" if last else "수집 기록이 없습니다"

    try:
        workers = await asyncio.to_thread(crawl_service.crawl_workers, WORKER_PING_SEC)
    except crawl_service.BrokerUnavailable:
        return StatusState.ABSENT, f"이 환경에는 크롤러가 없습니다 (브로커 없음). {when}."
    if not workers:
        return StatusState.ABSENT, f"이 환경에는 크롤러 워커가 떠 있지 않습니다. {when}."

    # `unavailable` 도 같이 셉니다 — "사람이 고쳐야 하는 것" 이라 `failed` 와 할 일이 같습니다
    # (`db/init` 의 `crawl_runs.status` 주석).
    failed = [r.source_id for r in runs if r.status in ("failed", "unavailable")]
    running = await crawl_service.running_count(session)
    detail = f"워커 {len(workers)}대. {when}. 소스 {len(runs)}개"
    if failed:
        return StatusState.DEGRADED, f"{detail}, 마지막 실행이 어긋난 소스 {len(failed)}개: {', '.join(failed[:5])}."
    if running:
        # 0 이 아니면 "지금 돌고 있거나, 워커가 죽어서 남았거나" 입니다 (`crawl_run.py`).
        return StatusState.DEGRADED, f"{detail}, 안 끝난 실행 {running}건 — 도는 중이거나 워커가 죽어 남은 것입니다."
    if last is not None and datetime.now(last.tzinfo) - last > CRAWL_STALE_AFTER:
        return StatusState.DEGRADED, f"{detail}. {CRAWL_STALE_AFTER.days}일 넘게 새 수집이 없습니다."
    return StatusState.OK, f"{detail}."


# ---------------------------------------------------------------- 다른 컨테이너에 묻기
async def _probe(base_url: str, paths: Sequence[str]) -> tuple[StatusState, str]:
    """다른 컨테이너의 헬스 경로를 두들깁니다.

    **이름이 안 풀리는 것을 `absent` 로 봅니다.** 개발 PC 의 `uv run dev` 에는 compose
    네트워크가 없어서 `place-search` 같은 이름이 애초에 없고, 그것은 고장이 아니라 "이
    환경엔 없다" 입니다. 이름은 풀렸는데 연결이 안 되면 그때가 `down` 입니다.

    한계는 `config.py` 의 주석에 적어 두었습니다 — compose 안에서 컨테이너가 아예 안 떠
    있으면 도커 DNS 도 이름을 못 풀어 `absent` 로 보입니다.
    """
    if not base_url:
        return StatusState.ABSENT, "주소가 설정돼 있지 않습니다."

    split = urlsplit(base_url)
    host, port = split.hostname, split.port or (443 if split.scheme == "https" else 80)
    if not host:
        return StatusState.DOWN, f"주소를 읽지 못했습니다: {base_url!r}"

    try:
        async with asyncio.timeout(DNS_TIMEOUT_SEC):
            await asyncio.get_running_loop().getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, TimeoutError):
        # **timeout 도 `absent` 로 봅니다** (2026-09-03 개발 PC 실측). 없는 이름이라고
        # 곧바로 `gaierror` 가 오지 않습니다 — Windows 는 상위 리졸버에 물어보느라 몇 초를
        # 씁니다. 그것을 `down` 으로 두면 개발 PC 에서 place·journey·gait 가 늘 빨갛고,
        # 그게 이 카드가 없애려던 바로 그 화면입니다.
        #
        # compose 안에서는 도커 DNS 가 같은 네트워크의 이름을 밀리초로 답하므로, 이 짧은
        # 시간을 못 지키는 것 자체가 "그 이름이 이 네트워크에 없다" 는 뜻입니다.
        return StatusState.ABSENT, f"이 환경엔 없습니다 — 이름 {host!r} 이 풀리지 않습니다."

    async with httpx.AsyncClient(base_url=base_url, timeout=1.5) as client:
        checked = []
        for path in paths:
            try:
                response = await client.get(path)
            except httpx.HTTPError as e:
                return StatusState.DOWN, f"{host} 에 닿지 못했습니다 — {type(e).__name__}: {e}"
            checked.append((path, response.status_code))

    bad = [f"{path} → {code}" for path, code in checked if code >= 400]
    if bad:
        # `/health` 는 되는데 `/health/ready` 만 안 되는 place 가 이 자리입니다 — 떠 있지만
        # 아직 받을 준비가 안 된 것이라 `down` 이 아닙니다.
        return StatusState.DEGRADED, f"{host}: " + ", ".join(bad)
    return StatusState.OK, f"{host} 정상 (" + ", ".join(p for p, _ in checked) + ")."
