"""캐시 — 발표 주기·stale·예산·single-flight (RT-001 ④ · ⑤-c, 구현 계획 8).

**네트워크를 타지 않는다.** `fetch` 자리에 세는 함수·터지는 함수를 넣으면 "언제 부르고 언제
안 부르는가"가 그대로 보인다 — 이 파일이 확인하는 것이 정확히 그것이다.

시각은 전부 고정값이다. `datetime.now()` 를 쓰면 "지금이 몇 분인가"에 따라 발표 주기 판정이
바뀌어 하루 중 특정 시간대에만 깨지는 테스트가 된다.
"""
from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta

import pytest

from daengs_life.realtime.cache import (
    CACHE_FILE,
    POLICY,
    Cache,
    Cached,
    Entry,
    MemoryStore,
    load_policy,
    split_key,
)
from daengs_life.realtime.config import KST
from daengs_life.realtime.observation import Source
from daengs_life.realtime.transport.base import NoData, Unavailable

# 모듈이 쥔 경로를 그대로 쓴다. 손으로 다시 조립하면 패키지가 움직일 때마다 어긋난다
# — 이관(D-018) 때 실제로 어긋났다.
CACHE_YAML = CACHE_FILE

NCST = Source.NCST.value
VILLAGE = Source.FCST_VILLAGE.value


def t(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=KST)


def counting(payload: object = {"ok": 1}):
    """호출 횟수를 세는 fetch. 리스트로 세는 이유는 클로저에서 쓰기 위해서다."""
    calls: list[int] = []

    def fetch():
        calls.append(1)
        return payload

    return fetch, calls


def boom(message: str = "죽었다"):
    def fetch():
        raise Unavailable(message)

    return fetch


# ------------------------------------------------------------- 발표 주기 (④-b)

def test_publish_times_come_from_the_measured_schedule() -> None:
    """④-b 는 임의 숫자가 아니라 실측 발표 시각에서 유도됐다 — 그 유도가 살아 있는지."""
    ncst = POLICY.feeds[NCST]
    assert ncst.next_publish(t(25, 14, 50)) == t(25, 15, 40)     # 매시 :40 제공
    assert ncst.next_publish(t(25, 14, 39)) == t(25, 14, 40)

    village = POLICY.feeds[VILLAGE]
    # 02·05·08·11·14·17·20·23시 발표 → :10 제공
    assert village.next_publish(t(25, 14, 50)) == t(25, 17, 10)
    assert village.next_publish(t(25, 23, 30)) == t(26, 2, 10)   # 날짜를 넘어간다


def test_staleness_counts_missed_publications_not_elapsed_time() -> None:
    """⑤-c 의 요점 — **시간이 아니라 발표 횟수**로 잰다.

    같은 59분이라도 그 사이에 발표가 있었으면 stale 이고 없었으면 신선하다. 경과 시간으로
    재면 발표 직후 58분된 값은 신선하다고 하고 발표 직전 2분된 값도 신선하다고 해서
    "새 값이 나왔는데 안 받았다"를 표현할 수 없다.
    """
    ncst = POLICY.feeds[NCST]
    assert ncst.missed(t(25, 13, 41), t(25, 14, 39)) == 0      # 58분, 발표 없음 → 신선
    assert ncst.missed(t(25, 14, 39), t(25, 14, 41)) == 1      # 2분, 발표 하나 지남 → stale
    assert ncst.missed(t(25, 11, 41), t(25, 14, 41)) == 3


def test_the_ttl_outlives_the_discard_limit() -> None:
    """④-b 가 셋을 분리한 이유 — TTL 이 폐기 상한보다 짧으면 옛 값이 먼저 사라져
    ⑤-c 가 얹힐 자리가 없어진다. 두 숫자의 **관계**가 결정이고, 값은 그 뒤다.
    """
    assert POLICY.ttl_periods > POLICY.discard_periods
    ncst = POLICY.feeds[NCST]
    assert ncst.ttl_sec(POLICY.ttl_periods) > ncst.period_min * POLICY.discard_periods * 60


# ---------------------------------------------------------------- 키 (④-a)

def test_the_key_is_the_lookup_not_the_user() -> None:
    """④-a — 격자로 캐시하면 한 번 호출이 그 격자의 모든 사용자를 덮는다."""
    assert Cache.key(NCST, "61,125") == "rt:kma-vilage-fcst:ncst:61,125"
    assert split_key("rt:kma-vilage-fcst:ncst:61,125") == (NCST, "61,125")


def test_the_key_survives_a_colon_inside_the_lookup() -> None:
    """조회 키에 `:` 가 들어온다 — 미세먼지 예보통보는 `'서울:2026-08-25'` 로 부른다."""
    key = Cache.key(Source.AIRKOREA_FORECAST.value, "서울:2026-08-25")
    assert split_key(key) == (Source.AIRKOREA_FORECAST.value, "서울:2026-08-25")


def test_a_historical_snapshot_key_includes_the_observation_cycle() -> None:
    assert Cache.snapshot_key(NCST, "61,125:202608250900") == (
        "rt-snapshot:kma-vilage-fcst:ncst:61,125:202608250900"
    )


def test_an_unknown_feed_fails_loudly() -> None:
    """설정을 빠뜨린 오퍼레이션에 기본 주기를 주면 그 조회만 영영 캐시가 안 맞는다."""
    with pytest.raises(KeyError, match="cache.yaml"):
        Cache(MemoryStore()).feed("없는-provider:없는-오퍼레이션")


# ------------------------------------------------------ 히트·미스 (④-b · ④-d)

def test_a_fresh_entry_does_not_call_the_api() -> None:
    cache = Cache(MemoryStore())
    fetch, calls = counting()
    assert cache.get(NCST, "61,125", fetch, t(25, 14, 41)).payload == {"ok": 1}
    assert cache.get(NCST, "61,125", fetch, t(25, 14, 59)).payload == {"ok": 1}
    assert len(calls) == 1, "발표가 없었는데 다시 불렀다"


def test_a_missed_publication_triggers_a_refetch() -> None:
    cache = Cache(MemoryStore())
    fetch, calls = counting()
    cache.get(NCST, "61,125", fetch, t(25, 14, 41))
    cache.get(NCST, "61,125", fetch, t(25, 15, 41))       # :40 발표를 하나 지났다
    assert len(calls) == 2


# ------------------------------------------------------------------ stale (⑤-c)

def test_a_failure_falls_back_to_the_old_value_and_says_so() -> None:
    """⑤-c — 버리지 않고 쓰되 **그 사실이 드러나야** ⑤-a 가 상한을 걸 수 있다."""
    cache = Cache(MemoryStore())
    fetch, _ = counting({"old": True})
    cache.get(NCST, "61,125", fetch, t(25, 14, 41))

    got = cache.get(NCST, "61,125", boom("HTTP 500"), t(25, 15, 41))
    assert got.payload == {"old": True} and got.stale is True
    assert "HTTP 500" in (got.reason or "") and "1회 놓침" in (got.reason or "")


def test_the_old_value_is_dropped_after_three_missed_publications() -> None:
    """폐기 상한은 ⑤-c 의 유일한 비인용 숫자이고 설정에 `우리 선택` 으로 표시돼 있다."""
    cache = Cache(MemoryStore())
    fetch, _ = counting({"old": True})
    cache.get(NCST, "61,125", fetch, t(25, 14, 41))

    at_two = cache.get(NCST, "61,125", boom(), t(25, 16, 41))
    assert at_two.payload == {"old": True} and at_two.stale

    at_three = cache.get(NCST, "61,125", boom(), t(25, 17, 41))
    assert at_three.payload is None and "폐기" in (at_three.reason or "")


def test_a_first_failure_with_no_history_is_simply_unknown() -> None:
    """옛 값이 없으면 stale 도 없다 — `UNKNOWN` 이지 `GOOD` 이 아니다 (⑤-a 가 이어받는다)."""
    got = Cache(MemoryStore()).get(NCST, "61,125", boom(), t(25, 14, 41))
    assert got.payload is None and got.stale is False and got.reason


# ------------------------------------------------------------------ 예산 (④-e)

def test_the_daily_budget_stops_the_calls_and_keeps_answering() -> None:
    """④-e 3 — 한도 초과로 전부 죽는 것보다 옛 값을 내는 편이 낫다."""
    store = MemoryStore()
    cache = Cache(store)
    fetch, calls = counting({"v": 1})
    cache.get(NCST, "61,125", fetch, t(25, 14, 41))

    group = POLICY.feeds[NCST].budget
    limit = POLICY.budgets[group]
    assert limit is not None
    for _ in range(limit):
        store.spend(group, "20260825")

    got = cache.get(NCST, "61,125", fetch, t(25, 15, 41))
    assert len(calls) == 1, "예산이 소진됐는데 호출했다"
    assert got.payload == {"v": 1} and got.stale and "예산" in (got.reason or "")


def test_a_failed_call_still_spends_the_budget() -> None:
    """실패도 한도를 먹는다 — 안 세면 죽은 API 를 하루 종일 두드리며 한도를 태운다."""
    store = MemoryStore()
    cache = Cache(store)
    cache.get(NCST, "61,125", boom(), t(25, 14, 41))
    assert store.used(POLICY.feeds[NCST].budget, "20260825") == 1


def test_the_budget_is_counted_per_kst_day() -> None:
    """data.go.kr 의 일 한도가 KST 축이다. UTC 로 세면 오전 9시에 한도가 초기화된다."""
    store = MemoryStore()
    cache = Cache(store)
    group = POLICY.feeds[NCST].budget
    limit = POLICY.budgets[group]
    assert limit is not None
    for _ in range(limit):
        store.spend(group, "20260825")
    assert cache.budget_left(NCST, t(25, 23, 30)) == 0
    assert cache.budget_left(NCST, t(26, 0, 30)) == limit          # 날이 바뀌면 되살아난다


def test_prefetch_that_is_not_allowed_to_call_still_serves_the_cache() -> None:
    """④-e 3 의 `allow_call=False` 경로. 프리페치를 멈춰도 요청은 답한다."""
    cache = Cache(MemoryStore())
    fetch, calls = counting({"v": 1})
    cache.get(NCST, "61,125", fetch, t(25, 14, 41))
    got = cache.get(NCST, "61,125", fetch, t(25, 15, 41), allow_call=False)
    assert len(calls) == 1 and got.stale and got.payload == {"v": 1}


# ---------------------------------------------------------- single-flight (④-d)

def test_concurrent_misses_call_the_api_once() -> None:
    """만료 순간 동시 요청이 같은 API 를 N번 때리는 것을 막는다 (④-d thundering herd)."""
    cache = Cache(MemoryStore())
    calls: list[int] = []
    started = threading.Barrier(8)

    def slow():
        calls.append(1)
        return {"v": len(calls)}

    def worker():
        started.wait()
        cache.get(NCST, "61,125", slow, t(25, 14, 41))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(calls) == 1, f"동시 8건이 {len(calls)}번 불렀다"


def test_a_historical_snapshot_is_reused_without_current_cycle_staleness() -> None:
    """과거 09시 관측은 12시에 다시 읽어도 현재 발표를 놓친 값이 아니다."""
    cache = Cache(MemoryStore())
    fetch, calls = counting({"observed": "09:00"})

    first = cache.get_snapshot(NCST, "61,125:202608250900", fetch, t(25, 10, 10))
    second = cache.get_snapshot(NCST, "61,125:202608250900", fetch, t(25, 12, 10))

    assert first.payload == second.payload == {"observed": "09:00"}
    assert first.calls == 1 and second.calls == 0
    assert len(calls) == 1


def test_different_historical_cycles_do_not_share_a_snapshot() -> None:
    cache = Cache(MemoryStore())
    fetch, calls = counting()

    cache.get_snapshot(NCST, "61,125:202608250900", fetch, t(25, 10, 10))
    cache.get_snapshot(NCST, "61,125:202608251000", fetch, t(25, 11, 10))

    assert len(calls) == 2


def test_historical_no_data_is_not_replaced_with_a_current_or_stale_value() -> None:
    """없는 과거값에 일반 ``rt:`` 캐시를 끼워 넣으면 일기가 현재 날씨로 오염된다."""
    cache = Cache(MemoryStore())
    current, _ = counting({"current": True})
    cache.get(NCST, "61,125", current, t(25, 10, 41))

    calls: list[int] = []

    def no_data():
        calls.append(1)
        raise NoData("그 시각 자료 없음")

    got = cache.get_snapshot(NCST, "61,125:202608250900", no_data, t(25, 10, 41))
    repeated = cache.get_snapshot(NCST, "61,125:202608250900", no_data, t(25, 10, 42))

    assert got.payload is None
    assert got.stale is False
    assert got.failure_kind == "nodata"
    assert got.calls == 1
    assert repeated.payload is None and repeated.failure_kind == "nodata"
    assert repeated.calls == 0
    assert calls == [1], "비재시도성 NoData를 같은 회차에 다시 호출했다"


def test_a_snapshot_does_not_unlock_another_process_flight() -> None:
    class BusyStore(MemoryStore):
        def __init__(self) -> None:
            super().__init__()
            self.unlocks = 0

        def lock(self, key: str, ttl_sec: int) -> bool:
            return False

        def unlock(self, key: str) -> None:
            self.unlocks += 1

    store = BusyStore()
    fetch, calls = counting()

    got = Cache(store).get_snapshot(
        NCST,
        "61,125:202608250900",
        fetch,
        t(25, 10, 41),
        wait_sec=0,
    )

    assert got.payload is None and got.failure_kind == "in_flight"
    assert calls == [], "다른 프로세스가 부르는 동안 중복 호출했다"
    assert store.unlocks == 0, "획득하지 않은 분산 락을 해제했다"


# ------------------------------------------------------------ 활성 키 (④-d · ④-f)

def test_a_request_marks_its_lookup_active() -> None:
    cache = Cache(MemoryStore())
    fetch, _ = counting()
    cache.get(NCST, "61,125", fetch, t(25, 14, 41))
    assert Cache.key(NCST, "61,125") in cache.active_keys(t(25, 14, 45))


def test_a_key_nobody_asked_for_in_a_day_falls_out() -> None:
    """④-d — 활성 집합이 곧 실사용 지역이라 안 쓰이면 스스로 빠져야 한다."""
    cache = Cache(MemoryStore())
    fetch, _ = counting()
    cache.get(NCST, "61,125", fetch, t(25, 14, 41))
    later = t(25, 14, 41) + timedelta(hours=POLICY.idle_drop_hours + 1)
    assert cache.active_keys(later) == []


def test_due_lists_only_the_keys_that_missed_a_publication() -> None:
    cache = Cache(MemoryStore())
    fetch, _ = counting()
    cache.get(NCST, "61,125", fetch, t(25, 14, 41))
    assert cache.due(t(25, 14, 50)) == []
    assert cache.due(t(25, 15, 41)) == [(NCST, "61,125")]


# ------------------------------------------------------------------ 저장물

def test_an_entry_survives_a_round_trip_as_text() -> None:
    """Redis 로 나가는 모양이다. 원본 응답을 담으므로 dict 도 문자열도 실린다."""
    entry = Entry({"items": [{"category": "T1H", "obsrValue": "33.1"}]}, t(25, 14, 40))
    assert Entry.loads(entry.dumps()) == entry
    assert Entry.loads(Entry("텍스트/CSV 한 줄", t(25, 14)).dumps()).payload == "텍스트/CSV 한 줄"


def test_a_negative_snapshot_entry_preserves_its_outcome() -> None:
    entry = Entry(None, t(25, 14), failure_kind="nodata", reason="그 시각 자료 없음")
    assert Entry.loads(entry.dumps()) == entry


def test_a_corrupt_entry_is_dropped_not_raised() -> None:
    """저장 포맷이 바뀐 옛 값 때문에 서비스가 죽으면 안 된다 — 버리고 다시 받는다."""
    assert Entry.loads("{이건 JSON 이 아니다") is None
    assert Entry.loads('{"payload": 1}') is None


def test_the_memory_store_honours_its_ttl() -> None:
    store = MemoryStore()
    store.set("rt:x:y:z", Entry({"v": 1}, t(25, 14)), ttl_sec=0)
    assert store.get("rt:x:y:z") is None


# ------------------------------------------------------------ 설정의 규율 (③-d 와 같은)

def test_every_source_has_a_publish_schedule() -> None:
    """`Source` 와 `cache.yaml` 이 같은 축이다 (④-a). 하나가 빠지면 그 조회만 캐시가 안 산다."""
    missing = [s.value for s in Source if s.value not in POLICY.feeds]
    assert not missing, missing


def test_every_uncited_number_is_marked_as_ours() -> None:
    """`thresholds.yaml` 과 같은 규율 (③-d) — 근거 없는 숫자를 조용히 박지 않는다.

    파트①의 KPI 가 "출처 링크 + 조항 번호 인용"인데 파트②에서 출처 없는 상수를 숨기면
    같은 기준을 스스로 어긴다.
    """
    text = CACHE_YAML.read_text(encoding="utf-8")
    # **설정 줄만 센다.** 헤더 주석도 같은 말을 쓰므로 파일 전체를 세면 문서를 한 줄
    # 다듬을 때마다 이 테스트가 깨지고, 그러면 숫자를 감시하는 것이 아니라 산문을 감시하게 된다
    marked = [l for l in text.splitlines() if re.match(r"^\s*[a-z_]+:.*우리 선택", l)]
    assert len(marked) == 3, f"비인용 숫자의 수가 바뀌었다 — 헤더 주석도 같이 고칠 것: {marked}"
    for name in ("discard_periods", "ttl_periods"):
        line = next(l for l in text.splitlines() if l.strip().startswith(name))
        assert "우리 선택" in line, f"{name} 에 표시가 없다"


def test_the_policy_file_actually_loads() -> None:
    policy = load_policy(CACHE_YAML)
    assert policy.active_keys == 10                     # ④-f 운영 상수
    assert policy.budgets["datagokr-vilage-fcst"] == 1000
    assert policy.budgets["apihub"] is None             # "모른다" 이지 무제한이 아니다


def test_a_cached_result_knows_whether_it_is_usable() -> None:
    assert Cached({"v": 1}).ok and not Cached(None).ok
