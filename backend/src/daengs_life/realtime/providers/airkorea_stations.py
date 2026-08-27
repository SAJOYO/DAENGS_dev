"""에어코리아 측정소 목록 — 조회 키를 만드는 provider (②-a 근거 2).

**방향이 다른 provider 다.** 다른 여섯은 룰이 읽을 값을 만들지만 이것은 **다음 호출의 인자**를
만든다. 그래서 산출물이 `Measurement` 가 아니라 `ResolvedLocation` 의 재료다.

**이 목록이 카카오를 대체 가능하게 만든다** (§6.5) — 응답에 좌표가 실려 오므로 하버사인으로
최근접을 직접 고를 수 있다. `transcoord → getNearbyMsrstnList` 두 번의 호출이 사라지고,
그만큼 일 1,000회 예산이 판정에 남는다.

신선도는 **월 1회**다 (이전·증설). `data/reference/` 에 캐시하는 유일한 이유이고, 그것이
`realtime` 이 `crawler.core.config` 의 경로 탐색을 참조하는 유일한 이유다 (RT-001 ①-2).
"""
from __future__ import annotations

from dataclasses import dataclass

from ..geo import Grid, LatLon, haversine_km, nearest, same_grid, to_grid
from ..transport import datagokr
from ..transport.base import Budget

PATH = "/B552584/MsrstnInfoInqireSvc"


@dataclass(frozen=True)
class Station:
    name: str
    point: LatLon
    addr: str = ""
    network: str = ""            # `mangName` — 도시대기 · 도로변대기 …

    @property
    def grid(self) -> Grid:
        return to_grid(self.point)


def parse_list(body: dict) -> list[Station]:
    """측정소 목록.

    ⚠️ **`dmX` 가 위도다.** 이름과 내용이 어긋나는 값이 실제로 들어온다 (§6.5) — `LatLon` 이
    네임드튜플인 이유가 이것이고, 뒤집힌 쌍은 틀린 격자를 내놓고 아무 예외도 안 낸다.
    """
    out: list[Station] = []
    for item in body.get("items") or []:
        name = str(item.get("stationName") or "").strip()
        try:
            point = LatLon(float(item["dmX"]), float(item["dmY"]))
        except (KeyError, TypeError, ValueError):
            continue                 # 좌표 없는 측정소는 고를 수 없다. 버리되 실패는 아니다
        if name:
            out.append(Station(name, point, str(item.get("addr") or "").strip(),
                               str(item.get("mangName") or "").strip()))
    return out


def pick(stations: list[Station], point: LatLon) -> tuple[Station, float] | None:
    """가장 가까운 측정소와 그 거리(km). 없으면 `None` → ⑤가 `AIR=UNKNOWN` 을 만든다.

    **여기서는 "가까우면"이 맞다.** ⑤-d 가 거리 임계를 기각한 것은 *같은 양의 출처를 고를 때*의
    이야기이고, 측정소는 애초에 하나만 고르는 조회 키다. 대안이 없으므로 임계가 생기지 않는다.
    """
    return nearest(point, stations, key=lambda s: s.point)


def in_grid(stations: list[Station], point: LatLon) -> list[Station]:
    """내 격자 안의 측정소들. ⑤-d 의 판정식을 그대로 쓴다."""
    return [s for s in stations if same_grid(point, s.point)]


# --- 받기와 파싱을 가른다 (RT-002 ②-a) ---------------------------------------
# 캐시가 저장하는 것은 **원본 응답**이라(`cache.py`) 조립층은 `raw_*` 로 받아 두었다가
# 히트일 때 같은 `parse_*` 를 다시 먹인다. URL·파라미터 지식은 provider 에 남는다.

def raw_list(addr: str, *, budget: Budget | None = None) -> dict:
    return datagokr.get(f"{PATH}/getMsrstnList", {"addr": addr, "numOfRows": 100}, budget=budget)


def fetch(addr: str, *, budget: Budget | None = None) -> list[Station]:
    """`addr` 는 `'서울'` 같은 시도명. 전국을 한 번에 받으려면 여러 번 부르거나 캐시를 쓴다."""
    return parse_list(datagokr.get(f"{PATH}/getMsrstnList",
                                   {"addr": addr, "numOfRows": 100}, budget=budget))


__all__ = ["PATH", "Station", "fetch", "haversine_km", "in_grid", "parse_list", "pick"]
