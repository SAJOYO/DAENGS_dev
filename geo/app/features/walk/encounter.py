"""세그먼트 열 × 시설 후보 → FacilityEncounter. 순수함수 — 기하값까지만.

"지나쳤다/봤다"는 만들지 않는다. 판정은 app/features/scene/judgment.py 가 이 사실 위에서
규칙표+버전으로 한다. 여기서는 게임 히트박스처럼 반지름별 원(10/15/20m)에 대해
체류 시간을 재는데, 틱은 GPS 점이고 원 경계 통과 시각은 선분 위에서 보간한다 —
체류가 점 간격보다 정밀해지는 이유다.

좌표 수학은 첫 세그먼트 기준 등장방형 투영(미터). 산책 반경(수 km)에서 오차는 cm 급.
"""

import math
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime

from app.features.walk.facts import Segment
from app.features.walk.models import FacilityEncounter, MotionEventOccurrence

BANDS_M = (10.0, 15.0, 20.0)
MAX_BAND_M = max(BANDS_M)
BOUNDARY_EPSILON = 1e-9


@dataclass(frozen=True)
class FacilityCandidate:
    """store 가 궤적 20m 버퍼로 추린 시설 한 곳. 존재 필터 없음 — 폐업도 관측 대상이다."""

    facility_source: str
    facility_ref: str
    kind: str
    lat: float
    lng: float
    place_active: bool | None
    as_of: date | None


@dataclass
class _OpenOccurrence:
    """한 연속 segment chain에서 열린 50m 원 진입. 함수 밖 계약은 아니다."""

    occurrence_index: int
    entered_at: datetime
    entered_offset_m: float
    entry_observed: bool
    exited_at: datetime
    exited_offset_m: float
    min_lateral_m: float = math.inf
    offset_at_min_m: float = 0.0
    dwell: dict[float, float] = field(default_factory=lambda: dict.fromkeys(BANDS_M, 0.0))
    accuracies: dict[int, float] = field(default_factory=dict)


def _projector(origin_lat: float, origin_lng: float):
    kx = 111_320.0 * math.cos(math.radians(origin_lat))
    ky = 111_320.0

    def project(lat: float, lng: float) -> tuple[float, float]:
        return ((lng - origin_lng) * kx, (lat - origin_lat) * ky)

    return project


def _inside_interval(ax, ay, bx, by, r: float) -> tuple[float, float] | None:
    """선분 P(t)=A+t(B-A), t∈[0,1] 가 원점 중심 반지름 r 원 안에 있는 t 구간."""
    dx, dy = bx - ax, by - ay
    a = dx * dx + dy * dy
    if a == 0:                                   # 제자리 — 점이 안이면 전체
        return (0.0, 1.0) if ax * ax + ay * ay <= r * r else None
    b = 2 * (ax * dx + ay * dy)
    c = ax * ax + ay * ay - r * r
    disc = b * b - 4 * a * c
    if disc < 0:
        return None
    sq = math.sqrt(disc)
    lo, hi = max((-b - sq) / (2 * a), 0.0), min((-b + sq) / (2 * a), 1.0)
    return (lo, hi) if lo < hi else None


def _closest(ax, ay, bx, by) -> tuple[float, float]:
    """(원점까지 최단거리, 그때의 t)."""
    dx, dy = bx - ax, by - ay
    a = dx * dx + dy * dy
    t = 0.0 if a == 0 else min(max(-(ax * dx + ay * dy) / a, 0.0), 1.0)
    px, py = ax + t * dx, ay + t * dy
    return math.hypot(px, py), t


def _at(seg: Segment, t: float) -> datetime:
    return seg.a.at + (seg.b.at - seg.a.at) * t


def _offset(seg: Segment, t: float) -> float:
    # 정지 구간은 위치가 흔들려도 이동거리 자가 진행하지 않는다.
    return seg.offset_m + (seg.dist * t if seg.moving else 0.0)


def _stop_facts(
    events: list[MotionEventOccurrence],
    project,
    fx: float,
    fy: float,
    entered_at: datetime,
    exited_at: datetime,
) -> tuple[dict[float, bool], int]:
    """같은 시설의 다른 통과에 정지를 복제하지 않도록 시간과 공간을 함께 겹친다."""
    overlap = dict.fromkeys(BANDS_M, False)
    stop_s_10 = 0.0
    for ev in events:
        overlap_start = max(entered_at, ev.started_at)
        overlap_end = min(exited_at, ev.ended_at)
        if overlap_end <= overlap_start:
            continue
        ex, ey = project(ev.lat, ev.lng)
        distance = math.hypot(ex - fx, ey - fy)
        for radius in BANDS_M:
            if distance <= radius:
                overlap[radius] = True
        if distance <= BANDS_M[0]:
            stop_s_10 += (overlap_end - overlap_start).total_seconds()
    return overlap, round(stop_s_10)


def compute_encounters(
    session_id: str,
    segments: list[Segment],
    events: list[MotionEventOccurrence],
    candidates: list[FacilityCandidate],
) -> list[FacilityEncounter]:
    if not segments or not candidates:
        return []
    project = _projector(segments[0].a.lat, segments[0].a.lng)

    found: list[FacilityEncounter] = []
    for cand in candidates:
        fx, fy = project(cand.lat, cand.lng)
        occurrence_index = 0
        current: _OpenOccurrence | None = None
        previous_chain: int | None = None
        seen_segment_in_chain = False

        def close_current(
            *,
            exit_observed: bool,
            candidate: FacilityCandidate = cand,
            facility_x: float = fx,
            facility_y: float = fy,
        ) -> None:
            nonlocal current, occurrence_index
            if current is None:
                return
            overlap, stop_s_10 = _stop_facts(
                events, project, facility_x, facility_y,
                current.entered_at, current.exited_at,
            )
            found.append(FacilityEncounter(
                session_id=session_id,
                event_index=0,                       # 전체 시간순 정렬 뒤 채운다
                occurrence_index=current.occurrence_index,
                entered_at=current.entered_at,
                exited_at=current.exited_at,
                entry_observed=current.entry_observed,
                exit_observed=exit_observed,
                entered_offset_m=round(current.entered_offset_m, 1),
                exited_offset_m=round(current.exited_offset_m, 1),
                facility_source=candidate.facility_source,
                facility_ref=candidate.facility_ref,
                kind=candidate.kind,
                lat=candidate.lat, lng=candidate.lng,
                place_active=candidate.place_active,
                as_of=candidate.as_of,
                min_lateral_m=round(current.min_lateral_m, 1),
                offset_m=round(current.offset_at_min_m, 1),
                dwell_s_10m=round(current.dwell[10.0]),
                dwell_s_15m=round(current.dwell[15.0]),
                dwell_s_20m=round(current.dwell[20.0]),
                pass_count=1,
                stop_overlap_10m=overlap[10.0],
                stop_overlap_15m=overlap[15.0],
                stop_overlap_20m=overlap[20.0],
                stop_s_10m=stop_s_10,
                accuracy_p50_m=(
                    round(statistics.median(current.accuracies.values()), 2)
                    if current.accuracies else None
                ),
            ))
            occurrence_index += 1
            current = None

        for seg in segments:
            if previous_chain is None or seg.chain_index != previous_chain:
                close_current(exit_observed=False)
                previous_chain = seg.chain_index
                seen_segment_in_chain = False

            ax, ay = project(seg.a.lat, seg.a.lng)
            bx, by = project(seg.b.lat, seg.b.lng)
            ax, ay, bx, by = ax - fx, ay - fy, bx - fx, by - fy

            iv_max = _inside_interval(ax, ay, bx, by, MAX_BAND_M)
            if iv_max is None:
                # 직전 segment가 경계에서 끝났고 이번 segment가 밖으로 나가면 이탈은 관측됐다.
                close_current(exit_observed=True)
                seen_segment_in_chain = True
                continue

            if current is None:
                entered_t = iv_max[0]
                current = _OpenOccurrence(
                    occurrence_index=occurrence_index,
                    entered_at=_at(seg, entered_t),
                    entered_offset_m=_offset(seg, entered_t),
                    # chain 첫 segment가 이미 원 안이면 실제 진입 경계는 수집되지 않았다.
                    entry_observed=(
                        entered_t > BOUNDARY_EPSILON or seen_segment_in_chain
                    ),
                    exited_at=_at(seg, iv_max[1]),
                    exited_offset_m=_offset(seg, iv_max[1]),
                )

            d, t = _closest(ax, ay, bx, by)
            if d < current.min_lateral_m:
                current.min_lateral_m = d
                current.offset_at_min_m = _offset(seg, t)

            for r in BANDS_M:
                iv = _inside_interval(ax, ay, bx, by, r)
                if iv:
                    current.dwell[r] += (iv[1] - iv[0]) * seg.dt
            for fix in (seg.a, seg.b):
                if fix.accuracy_m is not None:
                    current.accuracies[fix.client_seq] = fix.accuracy_m

            current.exited_at = _at(seg, iv_max[1])
            current.exited_offset_m = _offset(seg, iv_max[1])
            if iv_max[1] < 1.0 - BOUNDARY_EPSILON:
                close_current(exit_observed=True)
            seen_segment_in_chain = True

        # 수집 종료 또는 마지막 chain 단절 시점까지 원 안이었다.
        close_current(exit_observed=False)

    found.sort(key=lambda e: (e.entered_at, e.offset_m, e.facility_ref))
    return [e.model_copy(update={"event_index": i}) for i, e in enumerate(found)]
