"""세그먼트 열 × 사용자가 그린 면 → RegionEncounter. 순수함수 — DB·시계·난수 없음.

`app/features/walk/encounter.py` 의 면 버전이다. 거기가 시설 **점**에 반지름 원을 씌워
체류를 쟀다면, 여기는 **사용자가 그린 폴리곤** 안의 체류를 잰다. 둘의 차이는 도형이 아니라
답할 수 있는 문장이다:

    점 + 원   "그 앞을 지나갔다"      — 출입구를 모르니 `visited_guess` 가 최대치
    면        "그 안에 있었다"        — 경계가 곧 답이라 추정이 아니다

시설 원천은 폴리곤을 거의 주지 않는다(대개 중심점). 그래서 면은 **사용자가 그린다** —
공공데이터 커버리지 문제가 사라지고, 견주가 인식하는 경계("여기부터 목줄 풀어도 되는 데")는
어차피 어떤 원천에도 없다.

## 두 가지 판정을 같이 낸다

`exact`  세그먼트 × 폴리곤 변 교차. 원좌표가 살아 있는 finish 시점에만 가능하다.
`approx` 셀 방문 기록 × 폴리곤. 좌표가 purge 된 뒤에도, **면을 나중에 그려도** 답이 나온다.

같은 산책에 대해 둘을 함께 계산할 수 있는 것이 중요하다 — 그래야 근사가 얼마나 틀리는지
재고, 셀 반지름을 근거로 고를 수 있다 (`scripts/spikes/territory_paint/region_fidelity.py`).

좌표 수학은 첫 점 기준 등장방형 투영(미터). 산책 반경(수 km)에서 오차는 cm 급 —
`encounter.py` 와 같은 선택이다.
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import pairwise

from app.features.walk.facts import Segment
from app.geo.cells import (
    Cell,
    hex_cell,
    hex_center_latlng,
    hex_sample_points,
    inverse_mercator,
)

# 판정 규칙이 바뀌면 올린다. 사실(체류·occurrence)은 그대로 남고 판정만 다시 한다 —
# `scene/judgment.py` 의 JUDGMENT_VERSION 과 같은 성격이다.
REGION_OCCURRENCE_VERSION = 1

EARTH_R = 6_371_000.0


@dataclass(frozen=True)
class Region:
    """사용자가 그린 면 하나.

    **불변이다.** 사용자가 모양을 고치면 같은 `id` 의 새 `version` 이 되고 옛 행은 남는다.
    판정 결과가 `(id, version)` 을 함께 들고 있어야, 어제의 답이 오늘의 폴리곤 때문에
    조용히 뜻이 바뀌지 않는다 (#59 가 occurrence_version 으로 한 것과 같은 처리).

    `ring` 은 (lat, lng) 이고 닫지 않는다 — 마지막 점과 첫 점을 자동으로 잇는다.
    """

    id: str
    version: int
    ring: tuple[tuple[float, float], ...]

    def __post_init__(self) -> None:
        if len(self.ring) < 3:
            raise ValueError("면은 꼭짓점이 3개 이상이어야 한다")


@dataclass(frozen=True)
class RegionEncounter:
    """면 하나에 대한 **한 번의** 진입. 왕복하면 행이 둘이다.

    `encounter.py` 가 v1 에서 시설별 세션 합계를 한 행에 섞었다가 왕복·반복 통과를
    구분 못 해 `unjudgeable` 로 떨어뜨려야 했다(#59). 같은 실수를 처음부터 안 한다.
    """

    region_id: str
    region_version: int
    occurrence_index: int
    entered_at: datetime
    exited_at: datetime
    dwell_s: float
    entry_observed: bool          # False = 산책 시작 시점에 이미 안에 있었다
    exit_observed: bool           # False = 끝날 때까지 안에 있었다
    stop_overlap: bool = False    # 이 진입 중 정지(MotionEvent)와 겹쳤나
    occurrence_version: int = REGION_OCCURRENCE_VERSION


@dataclass
class CellVisit:
    """셀 하나에 머문 시간. purge 이후에도 남는 층 — 좌표가 아니라 격자 id 다."""

    cell: Cell
    dwell_s: float = 0.0
    first_at: datetime | None = None
    last_at: datetime | None = None


# ---- 기하 (투영 미터) ---------------------------------------------------------------


def _projector(lat0: float, lng0: float):
    """첫 점 기준 등장방형 투영. 반환 함수는 (lat,lng) → (x,y) 미터."""
    cos_lat = math.cos(math.radians(lat0))

    def project(lat: float, lng: float) -> tuple[float, float]:
        return (
            math.radians(lng - lng0) * EARTH_R * cos_lat,
            math.radians(lat - lat0) * EARTH_R,
        )

    return project


def _point_in_ring(x: float, y: float, ring: list[tuple[float, float]]) -> bool:
    """Ray casting. 경계 위의 점은 정의하지 않는다 — 세그먼트 분할이 경계를 피해 간다."""
    inside = False
    count = len(ring)
    for index in range(count):
        x1, y1 = ring[index]
        x2, y2 = ring[(index + 1) % count]
        if (y1 > y) != (y2 > y):
            t = (y - y1) / (y2 - y1)
            if x < x1 + t * (x2 - x1):
                inside = not inside
    return inside


def _edge_crossings(
    ax: float, ay: float, bx: float, by: float, ring: list[tuple[float, float]]
) -> list[float]:
    """선분 a→b 가 폴리곤 변을 지나는 매개변수 t ∈ (0,1) 들."""
    crossings: list[float] = []
    dx, dy = bx - ax, by - ay
    count = len(ring)
    for index in range(count):
        x1, y1 = ring[index]
        x2, y2 = ring[(index + 1) % count]
        ex, ey = x2 - x1, y2 - y1
        denom = dx * ey - dy * ex
        if abs(denom) < 1e-12:
            continue                       # 평행 — 겹침은 분할점을 만들지 않는다
        t = ((x1 - ax) * ey - (y1 - ay) * ex) / denom
        u = ((x1 - ax) * dy - (y1 - ay) * dx) / denom
        if 0.0 < t < 1.0 and 0.0 <= u <= 1.0:
            crossings.append(t)
    return crossings


# ---- exact: 세그먼트 × 폴리곤 --------------------------------------------------------


def region_encounters(
    segments: list[Segment],
    regions: list[Region],
    stop_windows: list[tuple[datetime, datetime]] | None = None,
) -> list[RegionEncounter]:
    """원좌표가 살아 있을 때의 정밀 판정. finish 시점에만 부를 수 있다.

    `encounter.py` 와 같은 제약이다 — purge 뒤엔 세그먼트가 없다. 그래서 이 함수의 결과와
    셀 방문 기록을 **같은 트랜잭션에서** 함께 남겨야 뒤에 그린 면과 비교할 수 있다.
    """
    if not segments or not regions:
        return []
    project = _projector(segments[0].a.lat, segments[0].a.lng)
    windows = stop_windows or []
    out: list[RegionEncounter] = []
    for region in regions:
        out += _encounters_for(segments, region, project, windows)
    return out


def _encounters_for(
    segments: list[Segment],
    region: Region,
    project,
    windows: list[tuple[datetime, datetime]],
) -> list[RegionEncounter]:
    """면 하나에 대한 진입 목록. 면마다 독립이라 따로 뗀다 — 상태가 섞이지 않는다."""
    ring = [project(lat, lng) for lat, lng in region.ring]
    out: list[RegionEncounter] = []
    occurrence_index = 0
    open_from: datetime | None = None
    open_dwell = 0.0
    entry_observed = True
    previous_chain: int | None = None

    def close(at: datetime, exit_observed: bool) -> None:
        nonlocal open_from, open_dwell, occurrence_index, entry_observed
        if open_from is None:
            return
        out.append(
            RegionEncounter(
                region_id=region.id,
                region_version=region.version,
                occurrence_index=occurrence_index,
                entered_at=open_from,
                exited_at=at,
                dwell_s=open_dwell,
                entry_observed=entry_observed,
                exit_observed=exit_observed,
                stop_overlap=any(s < at and open_from < e for s, e in windows),
            )
        )
        occurrence_index += 1
        open_from, open_dwell, entry_observed = None, 0.0, True

    for seg in segments:
        if previous_chain is not None and seg.chain_index != previous_chain:
            # 명시적 단절(pause·gap·jump). 끊긴 구간을 체류로 잇지 않는다.
            close(seg.a.at, exit_observed=False)
        previous_chain = seg.chain_index

        ax, ay = project(seg.a.lat, seg.a.lng)
        bx, by = project(seg.b.lat, seg.b.lng)
        marks = [0.0, *sorted(_edge_crossings(ax, ay, bx, by, ring)), 1.0]

        for lo, hi in pairwise(marks):
            if hi - lo < 1e-12:
                continue
            mid = (lo + hi) / 2
            inside = _point_in_ring(ax + (bx - ax) * mid, ay + (by - ay) * mid, ring)
            if inside:
                if open_from is None:
                    open_from = seg.a.at + timedelta(seconds=seg.dt * lo)
                    # lo==0 이고 이 세그먼트가 첫 세그먼트면 시작부터 안에 있었다
                    entry_observed = not (lo == 0.0 and seg is segments[0])
                    open_dwell = 0.0
                open_dwell += seg.dt * (hi - lo)
            elif open_from is not None:
                close(seg.a.at + timedelta(seconds=seg.dt * lo), exit_observed=True)

    if open_from is not None:
        close(segments[-1].b.at, exit_observed=False)
    return out


# ---- approx: 셀 방문 기록 × 폴리곤 ---------------------------------------------------


def cell_visits(segments: list[Segment], radius_u: float, step_m: float = 0.0) -> list[CellVisit]:
    """세그먼트 열 → 셀별 체류. purge 전에 만들어 두는 층이다.

    세그먼트를 `step_m` 간격으로 잘라 각 조각의 시간을 그 지점의 셀에 준다. 중점 하나로
    셀을 정하면 셀보다 긴 세그먼트가 통째로 한 셀에 몰린다 — 걸음 5초면 6m 지만 GPS 공백
    직후엔 수십 m 다. 기본 간격은 반지름의 1/4.
    """
    step = step_m or max(radius_u / 4.0, 2.0)      # 격자 단위 — 여기선 셀 자체가 자다
    accumulated: dict[Cell, CellVisit] = {}

    for seg in segments:
        pieces = max(1, math.ceil(seg.dist / step))
        for index in range(pieces):
            frac = (index + 0.5) / pieces
            lat = seg.a.lat + (seg.b.lat - seg.a.lat) * frac
            lng = seg.a.lng + (seg.b.lng - seg.a.lng) * frac
            cell = hex_cell(lat, lng, radius_u)
            at = seg.a.at + timedelta(seconds=seg.dt * frac)
            visit = accumulated.get(cell)
            if visit is None:
                visit = accumulated[cell] = CellVisit(cell=cell)
            visit.dwell_s += seg.dt / pieces
            visit.first_at = at if visit.first_at is None else min(visit.first_at, at)
            visit.last_at = at if visit.last_at is None else max(visit.last_at, at)

    return sorted(accumulated.values(), key=lambda v: (v.first_at or datetime.max.replace(tzinfo=UTC), v.cell))


def region_dwell_from_cells(
    visits: list[CellVisit],
    region: Region,
    radius_u: float,
    weighted: bool = True,
    rings: int = 3,
) -> float:
    """셀 방문 기록만으로 면 체류를 근사한다. **좌표 없이** 답이 나온다.

    `radius_u` 는 격자 **단위**다(`cells.py`). 이 함수는 셀 안팎만 보므로 단위를 미터로
    바꿀 필요가 없다 — 실제 크기를 말할 때만 `cells.cell_size_m` 을 거친다.

    `weighted=False` 는 셀 중심이 면 안이면 그 셀 시간을 통째로 준다 — 구현이 제일 싸고,
    셀이 면보다 훨씬 작으면 그걸로 충분하다. `True` 는 셀을 표본으로 채워 면과 겹치는
    비율만큼 준다. 어느 쪽이 필요한지는 셀/면 크기비가 정하므로 재고 고른다.
    """
    if not visits:
        return 0.0
    project = _projector(region.ring[0][0], region.ring[0][1])
    ring = [project(lat, lng) for lat, lng in region.ring]
    total = 0.0

    for visit in visits:
        if not weighted:
            lat, lng = hex_center_latlng(*visit.cell, radius_u)
            px, py = project(lat, lng)
            if _point_in_ring(px, py, ring):
                total += visit.dwell_s
            continue
        # 표본점은 메르카토르 평면이라 위경도를 거쳐 등장방형 평면으로 옮긴다.
        samples = hex_sample_points(*visit.cell, radius_u, rings)
        points = [inverse_mercator(mx, my) for mx, my in samples]
        hits = sum(1 for lat, lng in points if _point_in_ring(*project(lat, lng), ring))
        total += visit.dwell_s * (hits / len(points))

    return total


def dwell_by_region(encounters: list[RegionEncounter]) -> dict[str, float]:
    """면별 총 체류. exact 쪽 결과를 근사와 같은 자로 비교하려고 접는다."""
    totals: dict[str, float] = defaultdict(float)
    for e in encounters:
        totals[e.region_id] += e.dwell_s
    return dict(totals)


@dataclass
class RegionSpike:
    """스파이크 편의 묶음 — 계약이 아니다."""

    encounters: list[RegionEncounter] = field(default_factory=list)
    visits: list[CellVisit] = field(default_factory=list)
