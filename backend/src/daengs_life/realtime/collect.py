"""조립 — provider 들을 불러 `Observations` 하나로 (RT-002 ②-a).

**이 층이 아는 유일한 것은 호출 순서다.** providers 는 서로를 모르고(모듈 하나 = 서비스 하나),
rules 는 조회를 모른다(`judge(obs, t)` 는 시각의 함수다). 그 사이가 비어 있었고, 검문소 D 가
확인해야 하는 것 — **전송 실패가 캐시를 거쳐 판정까지 전달되는가** — 가 정확히 이 층이다.

순서에 결정 셋이 박혀 있다:

  1. **조회 키를 먼저 만든다** (②-a) — 격자는 계산으로 나오지만 측정소·AWS 지점은 목록을
     받아야 나온다. 그 둘은 월 1회짜리라 캐시 수명이 완전히 다르고, 없으면 그 축이 `UNKNOWN`
     이 된다.
  2. **같은 격자 안에 AWS 지점이 있으면 초단기실황을 생략한다** (④-e 1번) — ⑤-d 가 이미
     "같은 격자면 AWS 가 1순위"라고 정했으므로 실황을 불러도 지기만 한다. 격자당 56→32회/일.
     단, **AWS 가 실제로 값을 냈을 때만** 생략한다. 부르지도 않고 실패하면 둘 다 없어진다.
  3. **생활기상지수는 부르지 않는다** — ③-c 가 체감온도를 자체 계산으로 확정했고 UV 는
     ③-b 가 축에서 뺐다. 판정에 안 쓰이는 값에 일 예산을 쓰지 않는다. provider 는 산식을
     공식값과 **대조**할 때 쓰는 도구로 남는다 (§6.8 정정).

⑤-a 대로 **기상청 격자만 필수**다. 나머지는 없으면 `UNKNOWN` 이 되고, 그 상태로도 판정은
나가되 `GOOD` 으로 올라가지 않는다 — 그 상한은 여기가 아니라 `rules.judge` 가 건다.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .cache import Cache, split_key
from .config import REQUEST_BUDGET_SEC, STATIC_BUDGET_SEC
from .geo import Grid, LatLon, haversine_km, to_grid, to_latlon
from .observation import Measurement, Observations, ProviderResult, ResolvedLocation, Source, State
from .providers import (
    airkorea_realtime, airkorea_stations, kakao_local, kma_apihub, kma_vilage_fcst, kma_warning,
)
from .transport.base import Budget

# `getMsrstnList` 의 `addr` 는 시도 단축명이다 (실측 2026-08-25 — `세종` 6 · `강원` 40 · `전북` 48).
# **전국을 한 번에 주는 파라미터가 없다.** 그렇다고 17번 부르지는 않는다 —
# 콜드 캐시에서 실제로 재 보니 17회가 30초 예산을 다 먹고 판정을 못 냈다 (2026-08-25).
# 대신 **AWS 지점표에서 내 시도를 먼저 알아낸다**: 그 표는 어차피 ⑤-d 때문에 받고 있고
# `LAW_ADDR` 에 정식 주소가 실려 있다. 1~2회로 줄고, 카카오와도 무관하다
# (①이 카카오를 표기 전용으로 좁힌 것과 맞다). 아래 목록은 그 표가 없을 때의 폴백이다.
SIDO: tuple[str, ...] = (
    "서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종",
    "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주",
)


@dataclass
class _Run:
    """한 번의 조립. 캐시·예산·저하 기록을 들고 다닌다."""

    cache: Cache
    now: datetime
    # 정적 메타 전용 예산 — 요청 예산과 갈라 둔다. 콜드 캐시에서 지점표·측정소 목록이
    # ⑤-b 의 8초를 먹어 치우면 **판정 자체를 못 낸다.**
    static: Budget
    allow_call: bool = True
    results: list[ProviderResult] = field(default_factory=list)
    # ⑤-b 의 8초. **`Budget` 은 만들어진 순간부터 시계가 도는 값이라** 조립 시작 시점에
    # 만들면 안 된다 — 콜드 캐시에서 지점표를 받는 동안 이미 만료돼 판정 호출이 한 번도
    # 못 나간다 (실측 2026-08-25: 정적 17.4초 → 이후 7개 전부 "예산 초과"). 조회 키가
    # 다 만들어진 **뒤에** 시작한다.
    budget: Budget | None = None

    def start_request(self, given: Budget | None) -> Budget:
        self.budget = given if given is not None else Budget(REQUEST_BUDGET_SEC)
        return self.budget

    def payload(self, source: Source, lookup: str, fetch: Callable[[], Any]) -> Any | None:
        """캐시를 거쳐 원본 응답을 가져오고, **결과를 반드시 기록한다** (②-e).

        기록이 조건부이면 조용히 사라진 provider 가 생기고, ⑥의 `sources`(저하 이유)가
        "왜 모르는지"를 못 말한다.
        """
        got = self.cache.get(source.value, lookup, fetch, self.now,
                             allow_call=self.allow_call)
        self.results.append(ProviderResult(provider=source, ok=got.ok,
                                           reason=got.reason, stale=got.stale))
        return got.payload

    def parsed(self, source: Source, lookup: str, fetch: Callable[[], Any],
               parse: Callable[[Any], list]) -> list:
        """받기 → 파싱. **파싱 실패는 전송 실패와 다르게 다룬다** — 응답은 왔는데 우리가 못
        읽은 것이므로 캐시는 그대로 두고 이번 판정에서만 뺀다.
        """
        body = self.payload(source, lookup, fetch)
        if body is None:
            return []
        try:
            return parse(body)
        except Exception as exc:                       # noqa: BLE001 — provider 가 낼 수 있는 것 전부
            self.results[-1] = ProviderResult(provider=source, ok=False,
                                              reason=f"파싱 실패: {exc}")
            return []


# ------------------------------------------------------------------ 조회 키 (②-a)

def _stations(run: _Run, sidos: list[str]) -> list[airkorea_stations.Station]:
    """후보 시도의 측정소. 하나가 실패해도 나머지로 고른다 — 전부 실패해야 `AIR=UNKNOWN` 이다."""
    out: list[airkorea_stations.Station] = []
    failed: list[str] = []
    for sido in sidos:
        body = run.cache.get(Source.AIRKOREA_STATIONS.value, sido,
                             lambda s=sido: airkorea_stations.raw_list(s, budget=run.static),
                             run.now, allow_call=run.allow_call)
        if body.payload is None:
            failed.append(f"{sido}({body.reason})")   # 이유를 뭉개면 왜 못 골랐는지 못 말한다
            continue
        out += airkorea_stations.parse_list(body.payload)
    run.results.append(ProviderResult(
        provider=Source.AIRKOREA_STATIONS, ok=bool(out),
        reason=f"시도 {len(failed)}/{len(sidos)} 실패: {', '.join(failed)}" if failed else None))
    return out


def _aws_table(run: _Run) -> tuple[dict[int, LatLon], dict[int, str]]:
    """AWS 지점표 → (좌표, 시도). **한 응답에서 둘을 뽑는다** (RT-002 ②-c).

    좌표가 비면 ⑤-d 1순위가 아예 안 걸리고 2순위(격자 실황)로 내려간다.
    시도가 비면 아래에서 17개 폴백으로 떨어진다 — 느리지만 답은 나온다.
    """
    text = run.payload(Source.AWS_STATIONS, "all",
                       lambda: kma_apihub.raw_stations(budget=run.static))
    if not text:
        return {}, {}
    return kma_apihub.parse_stations(text), kma_apihub.station_addrs(text)


def _sido_candidates(coords: dict[int, LatLon], addrs: dict[int, str],
                     point: LatLon, k: int = 3) -> list[str]:
    """가장 가까운 AWS 지점 `k` 개의 시도. **하나가 아니라 셋을 보는 이유는 경계다** —
    바로 옆 시도의 측정소가 더 가까운 위치가 실제로 있다 (하남·과천 같은 서울 경계).
    """
    ranked = sorted(coords.items(), key=lambda kv: haversine_km(point, kv[1]))[:k]
    return list(dict.fromkeys(addrs[stn] for stn, _ in ranked if stn in addrs))


def _label(run: _Run, point: LatLon) -> str:
    """표기. **절대 판정을 막지 않는다** — 실패하면 좌표를 그대로 쓴다 (RT-001 ①)."""
    body = run.payload(Source.KAKAO, _point_key(point),
                       lambda: kakao_local.raw_region(point, budget=run.static))
    name = kakao_local.parse_region(body) if body else None
    return name or f"{point.lat:.4f}, {point.lon:.4f}"


def _point_key(point: LatLon) -> str:
    """표기용 캐시 키. **소수 3자리(≈100m)로 뭉갠다** — 행정동 이름은 그 해상도에서 안 변하고,
    좌표를 그대로 키로 쓰면 사람마다 다른 키가 되어 캐시가 한 번도 안 맞는다 (④-a 의 요지).
    """
    return f"{point.lat:.3f},{point.lon:.3f}"


def resolve(point: LatLon, run: _Run) -> tuple[ResolvedLocation, dict[int, LatLon]]:
    """좌표 → 다섯 개의 조회 키 (④-a 표)."""
    grid = to_grid(point)
    coords, addrs = _aws_table(run)
    stations = _stations(run, _sido_candidates(coords, addrs, point) or list(SIDO))

    picked = airkorea_stations.pick(stations, point) if stations else None
    station = picked[0] if picked else None
    aws = _aws_in_grid(coords, grid)

    return ResolvedLocation(
        point=point, grid=grid, label=_label(run, point),
        station=station.name if station else None,
        station_km=round(picked[1], 2) if picked else None,
        aws_station=str(aws) if aws is not None else None,
        # 특보구역 매핑표가 아직 없다 (🟡). 이름을 지어내지 않고 비워 둔다 —
        # `kma_warning` 은 아래 `_warning_areas` 가 넘기는 후보 이름으로 최선을 다한다
        warning_area=None,
        region=_region_of(station),
    ), coords


def _aws_in_grid(coords: dict[int, LatLon], grid: Grid) -> int | None:
    """같은 격자 안의 AWS 지점 하나. ⑤-d 의 "가까우면"이 아니라 "같은 격자면" 그대로다."""
    for stn, point in coords.items():
        if to_grid(point) == grid:
            return stn
    return None


def _region_of(station: airkorea_stations.Station | None) -> str | None:
    """미세먼지 예보 권역. 측정소 주소 첫 토큰이 시도 단축명이다 (`'서울 중구 …'`).

    권역명을 따로 지어내지 않는 이유 — 예보통보의 `informGrade` 가 `'서울 : 나쁨'` 처럼
    같은 단축명을 쓴다 (§6.3 실측).
    """
    if station is None or not station.addr:
        return None
    # **정식명이 그대로 오는 경우가 있다** (실측 — `강남대로` 의 addr 은 `서울특별시 …`).
    # 자르지 않고 넘기면 `informGrade` 의 `'서울 : 나쁨'` 과 영영 안 맞아 미세먼지 예보가
    # 조용히 한 번도 안 걸린다. 정규화는 `kma_apihub.sido_of` 한 곳에만 둔다
    return kma_apihub.sido_of(station.addr)


def _warning_areas(location: ResolvedLocation, station: str | None) -> list[str]:
    """특보 자연어에서 나를 찾을 후보 이름들.

    ⚠️ **매핑표가 없어서 최선 탐색이다** (🟡 대기 중). `t6` 은 `'서울동남권'` 같은 특보구역명을
    쓰는데 우리는 그 이름을 모른다. 시도 단축명(`'서울'`)은 대부분의 특보 문구에 그대로
    들어가므로 여기까지가 매핑표 없이 정직하게 할 수 있는 전부다 — 구 단위 특보는 놓친다.
    """
    names = [location.region, station, location.label]
    return [n for n in dict.fromkeys(names) if n]


# ------------------------------------------------------------------ 본체

def collect(point: LatLon, now: datetime, *, cache: Cache | None = None,
            budget: Budget | None = None, allow_call: bool = True) -> Observations:
    """좌표 하나에 대한 관측 한 묶음. `judge(collect(...), t)` 가 9단계 `app/` 의 전부다."""
    run = _Run(cache=cache if cache is not None else Cache(),
               now=now, static=Budget(STATIC_BUDGET_SEC), allow_call=allow_call)

    location, coords = resolve(point, run)
    run.start_request(budget)               # 조회 키가 다 나온 뒤에 ⑤-b 의 8초를 건다
    grid = location.grid
    gridkey = f"{grid.nx},{grid.ny}"

    measurements: list[Measurement] = []
    states: list[State] = []

    # --- ⑤-d 1순위 · ④-e 1번 -------------------------------------------------
    aws: list[Measurement] = []
    if coords:
        aws = run.parsed(Source.AWS_MIN, "all",
                         lambda: kma_apihub.raw_aws(now, budget=run.budget),
                         lambda text: kma_apihub.parse_aws(text, coords, grid))
        measurements += aws

    # --- 기상청 격자 (⑤-a 의 유일한 필수 출처) --------------------------------
    if not aws:
        # AWS 가 값을 냈을 때만 생략한다. 지점이 없거나 그 호출이 실패한 격자에서는
        # 실황이 유일한 현재 관측이라, 아껴 봐야 판정을 못 하게 된다
        measurements += run.parsed(Source.NCST, gridkey,
                                   lambda: kma_vilage_fcst.raw_ncst(grid, now, budget=run.budget),
                                   kma_vilage_fcst.parse_ncst)
    measurements += run.parsed(Source.FCST_ULTRA, gridkey,
                               lambda: kma_vilage_fcst.raw_ultra(grid, now, budget=run.budget),
                               kma_vilage_fcst.parse_ultra)
    measurements += run.parsed(Source.FCST_VILLAGE, gridkey,
                               lambda: kma_vilage_fcst.raw_village(grid, now, budget=run.budget),
                               kma_vilage_fcst.parse_village)

    # --- 대기질 ---------------------------------------------------------------
    if location.station:
        station = location.station
        measurements += run.parsed(Source.AIRKOREA, station,
                                   lambda: airkorea_realtime.raw_dnsty(station, budget=run.budget),
                                   lambda body: airkorea_realtime.parse_dnsty(body, station))
    if location.region:
        region = location.region
        states += run.parsed(Source.AIRKOREA_FORECAST, f"{region}:{now:%Y-%m-%d}",
                             lambda: airkorea_realtime.raw_frcst(now, budget=run.budget),
                             lambda body: airkorea_realtime.parse_frcst(body, region))

    # --- 특보 (전국 1세트라 조회 키가 하나다) ---------------------------------
    areas = _warning_areas(location, location.station)
    if areas:
        states += run.parsed(Source.WARNING, "all",
                             lambda: kma_warning.raw_pwn(budget=run.budget),
                             lambda body: kma_warning.parse_pwn(body, areas))

    return Observations(location=location, fetched_at=now, measurements=measurements,
                        states=states, providers=run.results)


# 격자를 조회 키로 쓰는 feed 들. 프리페치가 "무엇을 데울지" 고를 때의 축이다.
GRID_FEEDS = frozenset({Source.NCST.value, Source.FCST_ULTRA.value, Source.FCST_VILLAGE.value})


def active_grids(cache: Cache, now: datetime, limit: int | None = None) -> list[Grid]:
    """프리페치 대상 격자 (④-d · ④-f).

    **격자 단위로 센다.** ④-f 가 `N`=10 을 역산한 표가 "격자당 56회/일" 이므로 여기서 키를
    세면 그 계산과 단위가 어긋난다. 활성 키를 넉넉히 받아 격자로 접은 뒤 `N` 개를 자른다.
    """
    want = cache.policy.active_keys if limit is None else limit
    out: list[Grid] = []
    for key in cache.active_keys(now, limit=want * 8):
        parsed = split_key(key)
        if parsed is None or parsed[0] not in GRID_FEEDS:
            continue
        try:
            nx, ny = (int(v) for v in parsed[1].split(","))
        except ValueError:
            continue
        grid = Grid(nx, ny)
        if grid not in out:
            out.append(grid)
        if len(out) >= want:
            break
    return out


def warm(grid: Grid, now: datetime, *, cache: Cache, allow_call: bool = True) -> Observations:
    """격자 하나를 데운다. **격자 중심 좌표로 `collect` 를 그대로 돌린다** — 프리페치 전용
    경로를 따로 만들면 그 경로만 조용히 낡는다 (예: 새 provider 를 요청 경로에만 붙이는 날).
    """
    return collect(to_latlon(grid), now, cache=cache, allow_call=allow_call)


__all__ = ["GRID_FEEDS", "SIDO", "active_grids", "collect", "resolve", "warm"]
