"""신선도·캐시 — 언제 다시 부르고, 못 불렀을 때 무엇을 쓰나 (RT-001 ④ · ⑤-c).

**캐시하는 것은 판정 결과가 아니라 API 응답이다.** provider 가 `parse_*`(순수) + `fetch_*`(전송)
두 쪽으로 갈려 있으므로(6단계) 그 사이에 끼면 저장물이 dict/문자열이라 Redis 로 그대로 나가고,
파싱 규칙을 고쳐도 캐시를 비울 필요가 없다. 반대로 `Measurement` 를 캐시하면 ②-d 의 표현이
바뀔 때마다 저장물이 못 읽는 값이 된다.

**세 가지를 분리한다** (④-b) — 하나로 뭉치면 ⑤-c 가 얹힐 자리가 없어진다:

    TTL(보관)          Redis 가 값을 들고 있는 기간.       발표 주기 × `ttl_periods`
    재호출 시점        새 값이 생겼는가.                   `cache.yaml` 의 발표 주기
    사용 가능 여부     그 값을 판정에 써도 되는가.         놓친 발표 횟수 < `discard_periods`

**stale 은 예외가 아니라 예산 정책의 일부다** (⑤-c). 일 1,000회 제약에서는 "발표 주기가 지났지만
아직 못 불렀다"가 정상 경로다. 그래서 실패·예산초과·대기타임아웃을 구분하지 않고 전부 같은
자리(`ProviderResult.stale`)로 흘려보내고, ⑤-a 의 상한 규칙이 **`GOOD` 으로 올리는 것만** 막는다.

저장소는 Redis 지만 **없어도 돈다** (④-c). 캐시는 가속이지 정답의 원천이 아니다 — 연결이 없으면
프로세스 메모리로 떨어지고 매번 호출하며, ⑤-b 의 8초 예산이 그 비용을 잡는다.
"""
from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import yaml

from .config import KST, REDIS_URL
from .transport.base import TransportError

CACHE_FILE = Path(__file__).with_name("cache.yaml")

# 차분만 쓰므로 기준점이 어디든 상관없다. 자정(KST)에 맞춰 두면 `phase_min` 이
# "KST 자정에서 첫 발표까지의 분" 이라는 정의와 그대로 맞물린다.
_EPOCH = datetime(2020, 1, 1, tzinfo=KST)

PREFIX = "rt"


# ------------------------------------------------------------------ 발표 주기 (④-b)

@dataclass(frozen=True)
class Feed:
    """조회 하나 = provider:operation 하나. `Source` 와 **같은 축**이다 (④-a).

    ⑤-d 가 기온의 출처 넷을 우선순위로 세울 때 그중 셋이 같은 provider 라서 `Source` 가
    오퍼레이션까지 내려갔는데, 캐시 키도 같은 이유로 같은 깊이여야 한다 — 초단기실황과
    단기예보는 발표 주기가 다르므로 한 키에 묶이면 둘 중 하나는 반드시 틀린 주기로 갱신된다.
    """

    id: str
    period_min: float
    phase_min: float
    budget: str

    def cycle(self, when: datetime) -> int:
        """`when` 이 몇 번째 발표 구간에 있는가. 발표 횟수를 세는 눈금이다."""
        minutes = (when - _EPOCH).total_seconds() / 60.0
        return math.floor((minutes - self.phase_min) / self.period_min)

    def missed(self, since: datetime, now: datetime) -> int:
        """`since` 이후 지나간 발표 횟수. **시간이 아니라 횟수로 재는 것이 ⑤-c 의 요점이다.**"""
        return max(0, self.cycle(now) - self.cycle(since))

    def next_publish(self, now: datetime) -> datetime:
        """다음 발표 시각. Beat 가 언제 데울지를 여기서 가져간다 (④-d)."""
        nxt = self.cycle(now) + 1
        return _EPOCH + timedelta(minutes=self.phase_min + nxt * self.period_min)

    def ttl_sec(self, ttl_periods: float) -> int:
        return int(self.period_min * ttl_periods * 60)


@dataclass(frozen=True)
class Policy:
    feeds: dict[str, Feed]
    discard_periods: int
    ttl_periods: int
    budgets: dict[str, int | None]
    active_keys: int
    idle_drop_hours: int
    lock_sec: int


def load_policy(path: Path = CACHE_FILE) -> Policy:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    feeds = {fid: Feed(id=fid, period_min=float(spec["period_min"]),
                       phase_min=float(spec["phase_min"]), budget=str(spec["budget"]))
             for fid, spec in raw["feeds"].items()}
    stale, pre = raw["stale"], raw["prefetch"]
    return Policy(feeds=feeds,
                  discard_periods=int(stale["discard_periods"]),
                  ttl_periods=int(stale["ttl_periods"]),
                  budgets=dict(raw["budgets"]),
                  active_keys=int(pre["active_keys"]),
                  idle_drop_hours=int(pre["idle_drop_hours"]),
                  lock_sec=int(pre["lock_sec"]))


POLICY = load_policy()


# ------------------------------------------------------------------ 저장물

@dataclass(frozen=True)
class Entry:
    """API 응답 원본 + 받은 시각. 판정에 쓸 수 있는지는 `Feed.missed` 가 정한다."""

    payload: Any
    fetched_at: datetime

    def dumps(self) -> str:
        return json.dumps({"payload": self.payload, "fetched_at": self.fetched_at.isoformat()},
                          ensure_ascii=False)

    @staticmethod
    def loads(raw: str | bytes) -> Entry | None:
        try:
            data = json.loads(raw)
            return Entry(data["payload"], datetime.fromisoformat(data["fetched_at"]))
        except (ValueError, TypeError, KeyError):
            return None          # 저장 포맷이 바뀐 옛 값. 버리고 다시 받는다


@dataclass(frozen=True)
class Cached:
    """조회 하나의 결과. `collect` 가 이걸 `ProviderResult` 로 옮긴다 (②-e)."""

    payload: Any | None = None
    stale: bool = False
    reason: str | None = None
    calls: int = 0               # 이번에 실제로 API 를 부른 횟수

    @property
    def ok(self) -> bool:
        return self.payload is not None


# ------------------------------------------------------------------ 저장소 (④-c)

class Store(Protocol):
    """Redis 와 메모리가 같은 모양이라 `cache.py` 는 어느 쪽인지 모른다."""

    def get(self, key: str) -> Entry | None: ...
    def set(self, key: str, entry: Entry, ttl_sec: int) -> None: ...
    def used(self, group: str, day: str) -> int: ...
    def spend(self, group: str, day: str) -> int: ...
    def touch(self, key: str, at: datetime) -> None: ...
    def active(self, since: datetime, limit: int) -> list[str]: ...
    def lock(self, key: str, ttl_sec: int) -> bool: ...
    def unlock(self, key: str) -> None: ...


class MemoryStore:
    """프로세스 안에서만 사는 저장소. **Redis 가 없을 때의 정답이지 임시방편이 아니다** (④-c).

    한계는 정직하게 하나다 — 일 예산 카운터가 재시작하면 리셋된다. 그래서 운영에서는
    Redis 를 쓰고, 여기서는 그 사실을 숨기지 않는다.
    """

    def __init__(self) -> None:
        self._values: dict[str, tuple[Entry, float]] = {}
        self._budget: dict[tuple[str, str], int] = {}
        self._active: dict[str, datetime] = {}
        self._locks: set[str] = set()
        self._guard = threading.Lock()

    def get(self, key: str) -> Entry | None:
        with self._guard:
            found = self._values.get(key)
            if found is None:
                return None
            entry, expires = found
            if expires <= time.monotonic():
                del self._values[key]       # TTL 만료 — Redis 와 같게 보이도록 여기서 지운다
                return None
            return entry

    def set(self, key: str, entry: Entry, ttl_sec: int) -> None:
        with self._guard:
            self._values[key] = (entry, time.monotonic() + ttl_sec)

    def used(self, group: str, day: str) -> int:
        with self._guard:
            return self._budget.get((group, day), 0)

    def spend(self, group: str, day: str) -> int:
        with self._guard:
            count = self._budget.get((group, day), 0) + 1
            self._budget[(group, day)] = count
            return count

    def touch(self, key: str, at: datetime) -> None:
        with self._guard:
            self._active[key] = at

    def active(self, since: datetime, limit: int) -> list[str]:
        with self._guard:
            live = [(at, key) for key, at in self._active.items() if at >= since]
        return [key for _, key in sorted(live, reverse=True)[:limit]]

    def lock(self, key: str, ttl_sec: int) -> bool:
        with self._guard:
            if key in self._locks:
                return False
            self._locks.add(key)
            return True

    def unlock(self, key: str) -> None:
        with self._guard:
            self._locks.discard(key)


class RedisStore:
    """④-c 의 기본 저장소. 프로세스를 넘어 사는 것이 셋 있다 —
    값, **일 예산 카운터**, 활성 키 집합. 뒤의 둘이 Redis 를 쓰는 진짜 이유다:
    예산이 프로세스 메모리에 있으면 재시작마다 리셋이라 ④-e 의 제약이 시험되지 않고,
    활성 키가 프로세스 안에만 있으면 별도 프로세스인 Beat 가 그것을 읽지 못한다 (④-d).
    """

    def __init__(self, client: Any) -> None:
        self._r = client

    def get(self, key: str) -> Entry | None:
        raw = self._r.get(key)
        return Entry.loads(raw) if raw else None

    def set(self, key: str, entry: Entry, ttl_sec: int) -> None:
        self._r.set(key, entry.dumps(), ex=max(1, ttl_sec))

    def _budget_key(self, group: str, day: str) -> str:
        return f"{PREFIX}:budget:{group}:{day}"

    def used(self, group: str, day: str) -> int:
        raw = self._r.get(self._budget_key(group, day))
        return int(raw) if raw else 0

    def spend(self, group: str, day: str) -> int:
        key = self._budget_key(group, day)
        count = int(self._r.incr(key))
        if count == 1:
            self._r.expire(key, 60 * 60 * 48)    # 이틀 — 날짜가 넘어가면 카운터도 사라진다
        return count

    def touch(self, key: str, at: datetime) -> None:
        self._r.zadd(f"{PREFIX}:active", {key: at.timestamp()})

    def active(self, since: datetime, limit: int) -> list[str]:
        name = f"{PREFIX}:active"
        self._r.zremrangebyscore(name, "-inf", since.timestamp())   # ④-d 미요청 키 제거
        found = self._r.zrevrange(name, 0, max(0, limit - 1))
        return [k.decode() if isinstance(k, bytes) else str(k) for k in found]

    def lock(self, key: str, ttl_sec: int) -> bool:
        return bool(self._r.set(f"{PREFIX}:lock:{key}", "1", nx=True, ex=max(1, ttl_sec)))

    def unlock(self, key: str) -> None:
        self._r.delete(f"{PREFIX}:lock:{key}")


def open_store(url: str | None = None) -> Store:
    """Redis 가 있으면 Redis, 없으면 메모리. **연결 실패는 예외가 아니라 저하다** (④-c)."""
    url = REDIS_URL if url is None else url
    if not url:
        return MemoryStore()
    try:
        import redis                                     # 선택 의존이 아니라 지연 import 다 —
        client = redis.Redis.from_url(url)               # Redis 없이도 이 모듈이 import 돼야 한다
        client.ping()
        return RedisStore(client)
    except Exception:                                    # noqa: BLE001 — 연결 실패 종류를 안 가린다
        return MemoryStore()


# ------------------------------------------------------------------ 캐시 본체

class Cache:
    """조회 하나를 감싼다 — 히트면 즉시, 미스면 single-flight 로 한 번만 (④-d).

    **`single-flight` 를 두 겹으로 건다.** 프로세스 안은 `threading.Lock`(FastAPI 는 sync
    핸들러를 스레드풀에서 돌리므로 herd 가 실제로 여기서 난다), 프로세스 밖은 Redis 의 `SET NX`
    (웹 프로세스와 Beat 워커가 같은 키를 동시에 데울 수 있다). 한 겹만 걸면 배포 모양에 따라
    조용히 무력해진다.
    """

    def __init__(self, store: Store | None = None, policy: Policy = POLICY) -> None:
        self.store = store if store is not None else open_store()
        self.policy = policy
        self._threads: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    # -- 키 (④-a) --------------------------------------------------

    @staticmethod
    def key(feed_id: str, lookup: str) -> str:
        """`rt:{provider}:{operation}:{조회 키}`.

        **조회 키는 사용자 위치가 아니다** (④-a) — 격자 `61,125` 로 두면 강남 전역이 한 번
        호출로 덮이고, 역삼동으로 두면 같은 격자를 사람 수만큼 다시 받는다.
        """
        return f"{PREFIX}:{feed_id}:{lookup}"

    def feed(self, feed_id: str) -> Feed:
        try:
            return self.policy.feeds[feed_id]
        except KeyError:                          # 새 오퍼레이션을 붙이면서 설정을 빠뜨린 경우다.
            raise KeyError(                       # 조용히 기본값을 주면 그 조회만 영영 안 캐시된다
                f"`{feed_id}` 가 cache.yaml 의 feeds 에 없다. 발표 주기를 정하지 않으면 "
                f"stale 판정(⑤-c)도 재호출 시점(④-b)도 성립하지 않는다") from None

    # -- 예산 (④-e) ------------------------------------------------

    def budget_left(self, feed_id: str, now: datetime) -> int | None:
        """남은 일 호출 수. `None` 은 **한도를 모른다**이지 무제한이 아니다."""
        feed = self.feed(feed_id)
        limit = self.policy.budgets.get(feed.budget)
        if limit is None:
            return None
        return max(0, limit - self.store.used(feed.budget, _day(now)))

    # -- 본체 ------------------------------------------------------

    def get(self, feed_id: str, lookup: str, fetch: Callable[[], Any], now: datetime,
            *, allow_call: bool = True, wait_sec: float = 3.0) -> Cached:
        """캐시 → (필요하면) 호출 → stale 순으로 내려간다.

        `allow_call=False` 는 프리페치가 예산을 넘겼을 때 쓴다 (④-e 3) — 그때도 캐시된 값은
        그대로 낸다. 한도 초과로 전부 죽는 것보다 옛 값을 내는 편이 낫고, ⑤-a 가 그 값이
        `GOOD` 을 만들지 못하게 막는다.
        """
        feed = self.feed(feed_id)
        key = self.key(feed_id, lookup)
        self.store.touch(key, now)                # ④-d 활성 키 — 요청이 곧 실사용 지역이다

        entry = self.store.get(key)
        if entry is not None and feed.missed(entry.fetched_at, now) == 0:
            return Cached(entry.payload)          # 신선하다. 발표를 하나도 안 놓쳤다

        if not allow_call:
            return self._fallback(feed, entry, now, "프리페치 예산 초과 — 호출하지 않음")

        left = self.budget_left(feed_id, now)
        if left is not None and left <= 0:
            return self._fallback(feed, entry, now, f"일 예산 소진 ({feed.budget})")

        return self._flight(feed, key, fetch, now, entry, wait_sec)

    def _flight(self, feed: Feed, key: str, fetch: Callable[[], Any], now: datetime,
                entry: Entry | None, wait_sec: float) -> Cached:
        lock = self._thread_lock(key)
        with lock:
            fresh = self.store.get(key)           # 앞선 비행이 이미 채웠을 수 있다
            if fresh is not None and feed.missed(fresh.fetched_at, now) == 0:
                return Cached(fresh.payload)

            if not self.store.lock(key, self.policy.lock_sec):
                waited = self._await_other(feed, key, now, wait_sec)
                if waited is not None:
                    return Cached(waited.payload)
                # 기다리다 못 받았다. 그래도 부른다 — 판정을 못 하는 것보다 한 번 더 부르는
                # 편이 낫고, 락은 어차피 `lock_sec` 뒤에 스스로 풀린다
            try:
                payload = fetch()
                self.store.spend(feed.budget, _day(now))
                self.store.set(key, Entry(payload, now), feed.ttl_sec(self.policy.ttl_periods))
                return Cached(payload, calls=1)
            except TransportError as exc:
                self.store.spend(feed.budget, _day(now))   # 실패도 한도를 먹는다
                return self._fallback(feed, entry, now, str(exc), calls=1)
            finally:
                self.store.unlock(key)

    def _await_other(self, feed: Feed, key: str, now: datetime, wait_sec: float) -> Entry | None:
        """다른 비행이 채워 주기를 기다린다. 못 받으면 `None` — 그것도 정상 경로다 (⑤-c)."""
        deadline = time.monotonic() + max(0.0, wait_sec)
        while time.monotonic() < deadline:
            time.sleep(0.05)
            found = self.store.get(key)
            if found is not None and feed.missed(found.fetched_at, now) == 0:
                return found
        return None

    def _fallback(self, feed: Feed, entry: Entry | None, now: datetime, reason: str,
                  *, calls: int = 0) -> Cached:
        """새 값을 못 얻었다. **셋을 구분하지 않는다** (⑤-c) — 재시도 소진·예산 초과·대기
        타임아웃은 전부 "옛 값을 쓰거나 모른다"로 같다.
        """
        if entry is None:
            return Cached(None, reason=reason, calls=calls)
        missed = feed.missed(entry.fetched_at, now)
        if missed >= self.policy.discard_periods:
            return Cached(None, reason=f"{reason} · 옛 값도 폐기(발표 {missed}회 놓침)", calls=calls)
        return Cached(entry.payload, stale=True,
                      reason=f"{reason} · 옛 값 사용(발표 {missed}회 놓침)", calls=calls)

    def _thread_lock(self, key: str) -> threading.Lock:
        with self._guard:
            return self._threads.setdefault(key, threading.Lock())

    # -- 프리페치 (④-d · ④-f) --------------------------------------

    def active_keys(self, now: datetime, limit: int | None = None) -> list[str]:
        """Beat 가 데울 대상. **전국이 아니라 한 번이라도 쓰인 키만이다.**

        `limit` 을 열어 두는 이유 — ④-f 의 `N`=10 이 세는 단위는 **격자**이지 키가 아니다.
        격자 하나가 키를 여럿 만들므로(예보 3종 + 대기질 + …) 키 10개로 자르면 실제로는
        격자 서넛만 데워져 예산 계산이 통째로 어긋난다.
        """
        since = now - timedelta(hours=self.policy.idle_drop_hours)
        return self.store.active(since, self.policy.active_keys if limit is None else limit)

    def due(self, now: datetime) -> list[tuple[str, str]]:
        """지금 갱신이 필요한 활성 키 `(feed_id, lookup)`. 발표를 놓친 것만 고른다."""
        out: list[tuple[str, str]] = []
        for key in self.active_keys(now):
            parsed = split_key(key)
            if parsed is None or parsed[0] not in self.policy.feeds:
                continue
            feed_id, lookup = parsed
            entry = self.store.get(key)
            if entry is None or self.policy.feeds[feed_id].missed(entry.fetched_at, now) > 0:
                out.append((feed_id, lookup))
        return out


def split_key(key: str) -> tuple[str, str] | None:
    """`rt:kma-vilage-fcst:ncst:61,125` → `('kma-vilage-fcst:ncst', '61,125')`.

    provider·operation·조회 키가 전부 `:` 를 쓰므로 **앞에서 네 조각으로 자른다.** 조회 키
    안에 `:` 가 들어와도(측정소명 등) 뒤쪽은 그대로 붙는다.
    """
    parts = key.split(":", 3)
    if len(parts) != 4 or parts[0] != PREFIX:
        return None
    return f"{parts[1]}:{parts[2]}", parts[3]


def _day(now: datetime) -> str:
    """예산은 **KST 날짜**로 센다 — data.go.kr 의 일 한도가 그 축이다."""
    return now.astimezone(KST).strftime("%Y%m%d")


__all__ = ["Cache", "Cached", "Entry", "Feed", "MemoryStore", "POLICY", "Policy",
           "RedisStore", "Store", "load_policy", "open_store", "split_key"]
