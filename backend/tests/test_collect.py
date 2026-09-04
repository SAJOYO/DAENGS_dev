"""검문소 D — 저하 경로 (RT-001 구현 계획 · RT-002 ②-a).

**7단계가 이미 확인한 것은 여기서 다시 안 본다.** `judge` 가 모르는 축을 만났을 때 `GOOD` 으로
안 올린다는 것은 `test_rules.py` 가 손으로 만든 `Observations` 로 증명했다. 여기서 새로 보는
것은 **전송 실패가 조립·캐시를 거쳐 그 판정까지 실제로 흘러가는가**다 — 그 사이에 층이 셋
있고(`cache` → `collect` → `rules`), 어느 한 곳에서 실패를 삼키면 죽은 API 가 `GOOD` 이 된다.

입력은 검문소 B 와 같은 픽스처다 (2026-08-25 10:07, 강남 격자 61,125). 네트워크를 안 탄다 —
타면 "죽었을 때"를 재현할 수 없고, 그것이 이 파일의 전부다.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import pytest

from daengs_life.realtime.cache import Cache, MemoryStore
from daengs_life.realtime.collect import GRID_FEEDS, active_grids, collect, warm
from daengs_life.realtime.config import KST
from daengs_life.realtime.geo import Grid, LatLon
from daengs_life.realtime.observation import Source
from daengs_life.realtime.providers import (
    airkorea_realtime, airkorea_stations, kakao_local, kma_apihub, kma_vilage_fcst, kma_warning,
)
from daengs_life.realtime.rules import Axis, Grade, judge
from daengs_life.realtime.transport.base import Unavailable

FIXTURES = Path(__file__).parent / "fixtures" / "realtime"
HERE = LatLon(37.4979, 127.0276)
GRID = Grid(61, 125)
NOW = datetime(2026, 8, 25, 10, 10, tzinfo=KST)      # 픽스처 수집 시각 직후


def body(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))["response"]["body"]


def payload(name: str) -> dict:
    """카카오는 봉투가 없다 — 응답이 곧 본문이다."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def dead(*_a, **_k):
    raise Unavailable("죽었다", hint="검문소 D")


def _delayed(inner, seconds: float):
    """느린 provider. **`wired` 가 세는 것을 그대로 세도록 원래 스텁을 감싼다.**"""
    def slow(*a, **k):
        time.sleep(seconds)
        return inner(*a, **k)
    return slow


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    """provider 7개를 전부 픽스처로 바꾼다. `calls` 로 **무엇을 실제로 불렀는지** 센다."""
    calls: dict[str, list] = {}

    def stub(module, name, value):
        calls[f"{module.__name__.split('.')[-1]}.{name}"] = []

        def fake(*_a, **_k):
            calls[f"{module.__name__.split('.')[-1]}.{name}"].append(1)
            return value

        monkeypatch.setattr(module, name, fake)

    stub(kma_apihub, "raw_stations", text("kma-apihub.stations.txt"))
    stub(kma_apihub, "raw_aws", text("kma-apihub.aws.txt"))
    stub(airkorea_stations, "raw_list", body("airkorea-stations.list.json"))
    stub(kakao_local, "raw_region", payload("kakao-local.region.json"))
    stub(kma_vilage_fcst, "raw_ncst", body("kma-vilage-fcst.ncst.json"))
    stub(kma_vilage_fcst, "raw_ultra", body("kma-vilage-fcst.ultra.json"))
    stub(kma_vilage_fcst, "raw_village", body("kma-vilage-fcst.village.json"))
    stub(airkorea_realtime, "raw_dnsty", body("airkorea-realtime.dnsty.json"))
    stub(airkorea_realtime, "raw_frcst", body("airkorea-realtime.frcst.json"))
    stub(kma_warning, "raw_pwn", body("kma-warning.pwn.json"))
    return calls


def fresh() -> Cache:
    return Cache(MemoryStore())


def reasons(obs, source: Source) -> str:
    found = [r for r in obs.providers if r.provider is source]
    assert found, f"{source.value} 의 결과가 아예 기록되지 않았다"
    return f"ok={found[0].ok} stale={found[0].stale} {found[0].reason or ''}"


# ------------------------------------------------------------ 정상 경로 (기준선)

def test_the_whole_thing_assembles(wired) -> None:
    obs = collect(HERE, NOW, cache=fresh())
    assert obs.location.grid == GRID
    assert obs.location.station == "강남대로"          # §6.5 — 우리 격자 안의 최근접 측정소
    assert obs.location.region == "서울"               # `informGrade` 와 같은 표기여야 한다
    assert obs.measurements and obs.providers
    assert judge(obs, NOW).grade is not None


def test_every_provider_is_reported_even_when_it_worked(wired) -> None:
    """⑥의 `sources` 는 저하 이유만이 아니라 **무엇을 썼는지**의 목록이기도 하다 (②-e)."""
    obs = collect(HERE, NOW, cache=fresh())
    assert len(obs.providers) == len({r.provider for r in obs.providers}), "중복 기록"
    assert all(r.ok for r in obs.providers), [reasons(obs, r.provider) for r in obs.providers]


# ------------------------------------------- 검문소 D — 에어코리아를 죽였을 때

def test_killing_airkorea_makes_air_unknown_and_blocks_good(wired, monkeypatch) -> None:
    """**검문소 D 그 자체.** 대기질이 죽으면 `AIR` 은 모르는 축이 되고, 판정은 나가되
    `GOOD` 으로 올라가지 않는다 (⑤-a). 실패가 어느 층에서든 삼켜지면 여기서 잡힌다.
    """
    monkeypatch.setattr(airkorea_realtime, "raw_dnsty", dead)
    monkeypatch.setattr(airkorea_realtime, "raw_frcst", dead)

    obs = collect(HERE, NOW, cache=fresh())
    verdict = judge(obs, NOW)

    assert Axis.AIR in verdict.unknown_axes, reasons(obs, Source.AIRKOREA)
    assert verdict.grade is not Grade.GOOD, "모르는 축이 있는데 GOOD 이 나왔다"
    # `capped` 는 **상한이 실제로 답을 바꿨을 때만** True 다 — 이 픽스처는 열 축이 이미
    # CAUTION 이라 상한이 물리지 않는다. 그것이 ⑤-a 의 "위험은 감추지 않는다" 쪽이고,
    # 상한 자체는 `test_rules.py` 가 GOOD 이 될 뻔한 상황으로 이미 증명했다
    # 그리고 **왜 모르는지가 응답에 남아야 한다** — 안 남으면 사용자에게 "그냥 모른다"가 된다
    assert any(not r.ok and r.provider is Source.AIRKOREA for r in obs.providers)


def test_killing_the_station_list_also_costs_the_air_axis(wired, monkeypatch) -> None:
    """대기질 API 는 멀쩡한데 **조회 키를 못 만든** 경우다. 둘은 다른 사건이고 다르게 보고된다."""
    monkeypatch.setattr(airkorea_stations, "raw_list", dead)

    obs = collect(HERE, NOW, cache=fresh())
    assert obs.location.station is None and obs.location.region is None
    assert Axis.AIR in judge(obs, NOW).unknown_axes
    assert not [r for r in obs.providers if r.provider is Source.AIRKOREA], \
        "측정소를 못 골랐는데 대기질을 불렀다 — 확실히 실패할 호출로 예산을 태운다"
    assert any(not r.ok and r.provider is Source.AIRKOREA_STATIONS for r in obs.providers)


def test_killing_the_grid_provider_leaves_no_verdict_at_all(wired, monkeypatch) -> None:
    """⑤-a — 기상청 격자만 필수다. 없으면 판정 자체를 안 낸다."""
    for name in ("raw_ncst", "raw_ultra", "raw_village"):
        monkeypatch.setattr(kma_vilage_fcst, name, dead)
    monkeypatch.setattr(kma_apihub, "raw_aws", dead)

    assert judge(collect(HERE, NOW, cache=fresh()), NOW).grade is None


def test_a_dead_kakao_never_blocks_the_verdict(wired, monkeypatch) -> None:
    """①이 카카오를 표기 역할로 좁힌 것 — 표기 때문에 답을 못 주면 안 된다."""
    monkeypatch.setattr(kakao_local, "raw_region", dead)

    obs = collect(HERE, NOW, cache=fresh())
    assert obs.location.label == "37.4979, 127.0276"      # 좌표를 그대로 쓴다
    assert judge(obs, NOW).grade is not None


# ------------------------------------------------------------ 특보구역 (RT-003)
#
# `test_providers.py` 는 `["서울동남권", "서울"]` 을 **손으로 넘겨** 파서를 검증한다. 그래서
# 그 이름을 만들어 낼 방법이 없다는 사실을 가리고 있었다 — 이 절이 그 구멍을 막는다.
# 픽스처는 서초구(서초2동)이고 `t6` 는 이렇게 생겼다:
#
#     폭염경보   … 서울(서울동남권, 서울동북권) …      ← 서초구는 동남권이라 여기 걸린다
#     폭염주의보 … 서울(서울서남권, 서울서북권) …      ← 걸리면 안 된다

def test_the_district_is_mapped_to_its_warning_zone(wired) -> None:
    """카카오가 준 시군구가 특보구역명이 된다. 이것이 RT-003 의 전부다."""
    obs = collect(HERE, NOW, cache=fresh())
    assert obs.location.warning_areas == ("서울동남권",)
    assert obs.location.warning_area == "서울동남권"       # 표기용 대표 하나


def test_a_neighbouring_quadrant_warning_is_not_mine(wired) -> None:
    """묶음 머리(`서울(…)`)를 매칭하면 서초구가 서남권 주의보까지 자기 것으로 읽는다.

    경보와 주의보가 같은 축이라 이 픽스처에서는 등급이 안 변한다. 그래서 등급이 아니라
    **어느 특보가 잡혔는지**를 본다 — 옆 권역에만 주의보가 있는 날 근거 없는 CAUTION 이
    되는 것이 실제 피해다.
    """
    obs = collect(HERE, NOW, cache=fresh())
    warnings = [(s.kind.value, s.category) for s in obs.states if s.source is Source.WARNING]
    assert warnings == [("heat", "경보")]


def test_without_the_district_the_broad_name_still_catches(wired, monkeypatch) -> None:
    """카카오가 죽으면 시군구를 모른다. 그때까지 좁히면 RT-003 이 커버리지를 깎는다 —
    광역명으로 넓게 잡아 RT-003 이전과 같은 상태로 떨어진다.
    """
    monkeypatch.setattr(kakao_local, "raw_region", dead)

    obs = collect(HERE, NOW, cache=fresh())
    assert obs.location.warning_areas == ()
    kinds = {(s.kind.value, s.category) for s in obs.states if s.source is Source.WARNING}
    assert ("heat", "경보") in kinds and ("heat", "주의보") in kinds


def test_a_broken_response_is_reported_as_a_parse_failure(wired, monkeypatch) -> None:
    """응답은 왔는데 우리가 못 읽은 경우. 전송 실패와 섞으면 원인을 못 찾는다."""
    monkeypatch.setattr(airkorea_realtime, "raw_dnsty", lambda *a, **k: {"items": "리스트가 아니다"})

    obs = collect(HERE, NOW, cache=fresh())
    result = next(r for r in obs.providers if r.provider is Source.AIRKOREA)
    assert not result.ok and "파싱 실패" in (result.reason or "")


# ------------------------------------------------------ ⑤-d 1순위 · ④-e 1번

def test_a_working_aws_station_replaces_the_nowcast_call(wired) -> None:
    """④-e 의 가장 큰 절감 — 같은 격자 안 AWS 지점이 값을 내면 초단기실황을 안 부른다.

    격자당 56 → 32회/일. 이것이 안 걸리면 개발계정 1,000회로 서울을 못 덮는다.
    """
    calls = wired
    obs = collect(HERE, NOW, cache=fresh())
    assert obs.location.aws_station == "401", "우리 격자 안 AWS 지점을 못 찾았다"
    assert calls["kma_vilage_fcst.raw_ncst"] == [], "AWS 가 있는데 초단기실황을 불렀다"
    assert calls["kma_apihub.raw_aws"] == [1]


def test_a_dead_aws_falls_back_to_the_nowcast(wired, monkeypatch) -> None:
    """**부르지도 않고 실패하면 둘 다 없어진다.** 생략은 AWS 가 값을 냈을 때만이다."""
    monkeypatch.setattr(kma_apihub, "raw_aws", dead)
    calls = wired

    obs = collect(HERE, NOW, cache=fresh())
    assert calls["kma_vilage_fcst.raw_ncst"] == [1], "AWS 가 죽었는데 실황도 안 불렀다"
    assert judge(obs, NOW).grade is not None


def test_without_the_station_table_the_first_priority_never_fires(wired, monkeypatch) -> None:
    """RT-002 ②-c 이전의 상태다 — 지점표가 없으면 ⑤-d 1순위가 통째로 미발동한다."""
    monkeypatch.setattr(kma_apihub, "raw_stations", dead)
    calls = wired

    obs = collect(HERE, NOW, cache=fresh())
    assert obs.location.aws_station is None
    assert calls["kma_apihub.raw_aws"] == [], "좌표표가 없는데 AWS 를 불렀다"
    assert calls["kma_vilage_fcst.raw_ncst"] == [1], "2순위(격자 실황)로 안 내려갔다"


# ---------------------------------------------------------------- 캐시 (④)

def test_the_second_request_calls_nothing(wired) -> None:
    """④-a 의 값 — 같은 격자의 두 번째 사용자는 API 를 한 번도 안 부른다."""
    calls = wired
    cache = fresh()
    collect(HERE, NOW, cache=cache)
    before = {k: len(v) for k, v in calls.items()}
    collect(HERE, NOW, cache=cache)
    assert {k: len(v) for k, v in calls.items()} == before


def test_a_neighbour_in_the_same_grid_reuses_the_same_calls(wired) -> None:
    """④-a 의 요지 그대로 — "역삼동에 대한 캐시" 였다면 여기서 전부 다시 받는다."""
    calls = wired
    cache = fresh()
    collect(HERE, NOW, cache=cache)
    before = {k: len(v) for k, v in calls.items()}
    collect(LatLon(37.5010, 127.0300), NOW, cache=cache)      # 같은 격자, 다른 좌표
    after = {k: len(v) for k, v in calls.items()}
    for name in ("kma_vilage_fcst.raw_village", "kma_vilage_fcst.raw_ultra", "kma_apihub.raw_aws"):
        assert after[name] == before[name], f"{name} 을 같은 격자인데 다시 불렀다"
    # **대기질은 다시 부를 수 있고 그것이 맞다** — 조회 키가 격자가 아니라 측정소라 이웃이
    # 다른 측정소를 고를 수 있다. ④-a 의 "위치가 다섯 개의 공간 단위로 갈라진다"가 이것이다


# -------------------------------------------------------- 프리페치 (④-d · ④-f)

def test_prefetch_targets_only_grids_someone_asked_for(wired) -> None:
    """④-d — 전국을 미리 당기지 않는다. 활성 집합이 곧 실사용 지역이다."""
    cache = fresh()
    assert active_grids(cache, NOW) == []
    collect(HERE, NOW, cache=cache)
    assert active_grids(cache, NOW) == [GRID]


def test_prefetch_counts_grids_not_keys(wired) -> None:
    """④-f 의 `N`=10 이 세는 단위는 격자다. 키로 세면 격자 서넛만 데워진다."""
    cache = fresh()
    collect(HERE, NOW, cache=cache)
    keys = [k for k in cache.active_keys(NOW, limit=100)
            if k.split(":", 3)[1] + ":" + k.split(":", 3)[2] in GRID_FEEDS]
    assert len(keys) >= 2 and active_grids(cache, NOW) == [GRID]


# ------------------------------------------------------------ 병렬 (⑤-b)
#
# 콜드 캐시에서 `sources` 9개 중 **마지막 3개**(`dnsty`·`frcst`·`pwn`)가 늘 `예산 초과` 로
# 죽었다. 그 셋이 느려서가 아니라 호출 순서의 마지막이라서였다 — 직렬이면 8초가 **총합**이라
# 앞의 셋이 다 먹으면 뒤는 호출조차 안 나간다. 예산을 늘리는 것으로는 안 고쳐진다:
# `transport/base.py` 의 재시도 때문에 provider 하나가 남은 예산을 혼자 다 먹을 수 있다.

def test_the_request_wave_goes_out_in_parallel(wired, monkeypatch) -> None:
    """**벽시계로 잰다.** 예산은 스텁이 `transport` 를 건너뛰어서 여기까지 안 내려온다 —
    직렬/병렬을 가르는 것은 "예산 초과가 났는가"가 아니라 조회 다섯이 겹쳐 나갔는가다.

    직렬이면 5 × 0.3 = 1.5초 이상, 병렬이면 0.3초 언저리다. 0.9초는 그 사이 어디를 잡아도
    안 흔들리는 자리라 고른 값이다 — 느린 PC 에서도 병렬은 0.9초를 안 넘고, 빠른 PC 에서도
    직렬은 1.5초 밑으로 못 내려온다.
    """
    slow = ("kma_apihub.raw_aws", "kma_vilage_fcst.raw_ultra", "kma_vilage_fcst.raw_village",
            "airkorea_realtime.raw_dnsty", "airkorea_realtime.raw_frcst", "kma_warning.raw_pwn")
    for name in slow:
        module_name, attr = name.split(".")
        module = {"kma_apihub": kma_apihub, "kma_vilage_fcst": kma_vilage_fcst,
                  "airkorea_realtime": airkorea_realtime, "kma_warning": kma_warning}[module_name]
        original = getattr(module, attr)
        monkeypatch.setattr(module, attr, _delayed(original, 0.3))

    started = time.monotonic()
    obs = collect(HERE, NOW, cache=fresh())
    elapsed = time.monotonic() - started

    assert elapsed < 0.9, f"조회가 겹쳐 나가지 않았다 — {elapsed:.2f}초 걸렸다 (직렬이면 1.5초+)"
    assert all(r.ok for r in obs.providers), [reasons(obs, r.provider) for r in obs.providers]


def test_a_parse_failure_only_marks_its_own_provider(wired, monkeypatch) -> None:
    """**병렬화에서 실제로 고쳐야 했던 자리.**

    예전 `parsed()` 는 파싱 실패를 `self.results[-1]` 에 썼다. 직렬일 때는 그 `[-1]` 이 방금
    내가 넣은 항목이었지만, 병렬에서는 "마지막에 append 한 남의 항목"이라 **엉뚱한 provider 를
    실패로 만들고 진짜 실패는 사라진다.** 완료 순서를 일부러 어긋나게 만들어 그것을 잡는다 —
    깨진 응답은 즉시 오고, 다른 조회들은 늦게 온다.
    """
    monkeypatch.setattr(airkorea_realtime, "raw_dnsty", lambda *a, **k: {"items": "리스트가 아니다"})
    for module, attr in ((kma_vilage_fcst, "raw_ultra"), (kma_vilage_fcst, "raw_village"),
                         (kma_warning, "raw_pwn")):
        monkeypatch.setattr(module, attr, _delayed(getattr(module, attr), 0.2))

    obs = collect(HERE, NOW, cache=fresh())

    failed = [r.provider for r in obs.providers if not r.ok]
    assert failed == [Source.AIRKOREA], f"파싱 실패가 남의 자리를 덮었다 — {failed}"
    assert "파싱 실패" in (next(r for r in obs.providers if r.provider is Source.AIRKOREA).reason or "")


def test_the_source_list_keeps_a_fixed_order(wired) -> None:
    """⑥ `sources` 는 완료 순서가 아니라 **선언 순서**다.

    병렬이면 그날의 응답 속도가 곧 목록 순서가 되어, 콘솔에서 어제와 오늘을 눈으로 못 맞춘다.
    순서는 병렬화 이전의 호출 순서와 같게 두었다 — 화면에 보이는 것은 안 바뀐다.
    """
    obs = collect(HERE, NOW, cache=fresh())
    assert [r.provider for r in obs.providers] == [
        Source.AWS_STATIONS, Source.AIRKOREA_STATIONS, Source.KAKAO,
        Source.AWS_MIN, Source.FCST_ULTRA, Source.FCST_VILLAGE,
        Source.AIRKOREA, Source.AIRKOREA_FORECAST, Source.WARNING,
    ]   # AWS 가 값을 냈으므로 `ncst` 는 안 불렀다 (④-e 1번)


def test_the_daily_budget_counts_every_concurrent_call_exactly_once(wired) -> None:
    """④-e — 동시에 나가도 일 예산은 호출 수와 정확히 같아야 한다.

    카운터가 병렬에서 유실되면 **한도를 넘겨 쓰고도 안 넘긴 줄 안다.** data.go.kr 1,000회/일이
    이 카운터 하나로 지켜지므로(개발계정, 2026-08-25 결정) 여기가 틀리면 조용히 차단된다.
    `MemoryStore` 는 `_guard` 락, `RedisStore` 는 `INCR` 로 원자적이라는 것의 확인이다.
    """
    calls = wired
    cache = fresh()
    collect(HERE, NOW, cache=cache)

    made = {name: len(hits) for name, hits in calls.items()}
    day = NOW.strftime("%Y%m%d")        # `cache._day` 와 같은 축이어야 한다 (KST 날짜)
    assert cache.store.used("datagokr-vilage-fcst", day) == (
        made["kma_vilage_fcst.raw_ultra"] + made["kma_vilage_fcst.raw_village"]
        + made["kma_vilage_fcst.raw_ncst"])
    assert cache.store.used("datagokr-airkorea", day) == (
        made["airkorea_realtime.raw_dnsty"] + made["airkorea_realtime.raw_frcst"])
    assert cache.store.used("datagokr-warning", day) == made["kma_warning.raw_pwn"]
    assert cache.store.used("apihub", day) == (
        made["kma_apihub.raw_aws"] + made["kma_apihub.raw_stations"])


def test_warming_uses_the_same_path_as_a_request(wired) -> None:
    """프리페치 전용 경로를 따로 두면 그 경로만 조용히 낡는다."""
    calls = wired
    cache = fresh()
    warm(GRID, NOW, cache=cache)
    assert calls["kma_vilage_fcst.raw_village"] == [1]
    before = {k: len(v) for k, v in calls.items()}
    collect(HERE, NOW, cache=cache)                    # 데워진 격자는 호출이 없다
    after = {k: len(v) for k, v in calls.items()}
    # 표기(카카오)만 예외다 — 조회 키가 좌표(≈100m)라 격자 중심으로 데울 수 없다.
    # 판정에는 안 쓰이므로 프리페치 대상이 아니다 (RT-001 ①: 카카오는 UX 역할)
    del after["kakao_local.raw_region"], before["kakao_local.raw_region"]
    assert after == before
