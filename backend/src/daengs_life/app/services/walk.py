"""`/walk` 유스케이스 — 도메인 호출 + ⑥ 계약으로 옮기기 (RAG-027 의 service 층).

**컨트롤러에 로직을 안 넣는다는 규칙이 이 파일을 만든다.** 여기서 하는 일은 셋뿐이고
그중 어느 것도 판정이 아니다:

  1. 도메인을 부른다      `collect` → `judge` · `timeline` · `windows`
  2. 계약으로 옮긴다      `Verdict` → `WalkOut` (도메인 변화가 HTTP 로 새지 않게)
  3. 사람이 읽을 것을 만든다   `label` 정직 표기 · `notes`

**판정은 한 줄도 여기 없다.** 등급을 만드는 것은 `rules.judge` 뿐이고, 이 층이 등급을
손대기 시작하면 ③ 전체가 두 곳에 살게 된다.
"""
from __future__ import annotations

from datetime import datetime

from daengs_life.app.dto.walk import (
    AxisOut, BasisState, BasisValue, LocationOut, SourceOut, TimelinePoint, VerdictOut,
    WalkOut, WindowOut,
)
from daengs_life.realtime.cache import Cache
from daengs_life.realtime.collect import collect
from daengs_life.realtime.geo import LatLon
from daengs_life.realtime.observation import Code, Interval, Measurement, Observations, ResolvedLocation, State
from daengs_life.realtime.rules import AxisVerdict, Verdict, judge, timeline, windows

HORIZON_HOURS = 24          # ③-a 의 상한. 그 안의 판정 가능 여부는 데이터가 정한다


def walk(point: LatLon, now: datetime, *, cache: Cache) -> WalkOut:
    """좌표 하나 → ⑥ 응답 하나. 9단계의 본체가 이 세 줄이다."""
    obs = collect(point, now, cache=cache)
    verdicts = timeline(obs, now, hours=HORIZON_HOURS)
    spans = windows(verdicts)

    return WalkOut(
        location=_location(obs.location),
        generated_at=obs.fetched_at,
        now=_verdict(judge(obs, now), detailed=True),
        timeline=[TimelinePoint(at=v.at, grade=_grade(v.grade),
                                dominant=[a.value for a in v.dominant]) for v in verdicts],
        windows=[WindowOut(**{"from": lo, "to": hi, "grade": _span_grade(verdicts, lo, hi)})
                 for lo, hi in spans],
        sources=[SourceOut(provider=r.provider.value, ok=r.ok, stale=r.stale, reason=r.reason)
                 for r in obs.providers],
        notes=_notes(obs),
    )


# ---------------------------------------------------------------- 계약으로 옮기기

def _grade(grade) -> str:
    """⑥ — 판정 불가는 `null` 이 아니라 `"unknown"` 이다. `null` 은 "필드 없음"과 헷갈린다."""
    return grade.name if grade is not None else "unknown"


def _span_grade(verdicts: list[Verdict], lo: datetime, hi: datetime) -> str:
    """구간의 등급 = 그 안에서 **가장 낮은** 등급.

    `windows` 는 "`at_least` 이상이 이어지는" 구간이라 안쪽이 전부 같은 등급이라는 보장이
    없다. 최고를 쓰면 구간 전체를 실제보다 좋게 말하게 된다 — ⑤-a 가 막으려던 것과 같은 종류다.
    """
    inside = [v.grade for v in verdicts if lo <= v.at <= hi and v.grade is not None]
    return min(inside).name if inside else "unknown"


def _location(loc: ResolvedLocation) -> LocationOut:
    return LocationOut(
        dong=loc.label, grid=(loc.grid.nx, loc.grid.ny),
        air_station=loc.station, air_station_km=loc.station_km,
        aws_station=loc.aws_station, warning_zone=loc.warning_area,
        label=label_for(loc),
    )


def label_for(loc: ResolvedLocation) -> str:
    """**서버가 만드는 정직 표기** (⑥ · `realtime-apis.md` §0).

    GPS 는 점이지만 답은 격자·측정소 단위다. 그 간극을 숨기면 사용자는 자기 위치의 값이라고
    믿는다. 그래서 무엇을 기준으로 답했는지를 문장에 넣고, **클라이언트에 맡기지 않는다.**
    """
    if loc.station:
        return f"{loc.label} (측정소: {loc.station}) 기준"
    return f"{loc.label} 기준"


def _verdict(v: Verdict, *, detailed: bool) -> VerdictOut:
    return VerdictOut(
        at=v.at, grade=_grade(v.grade),
        dominant=[a.value for a in v.dominant],
        axes={axis.value: _axis(av, detailed=detailed) for axis, av in v.axes.items()},
        unknown_axes=[a.value for a in v.unknown_axes],
        capped=v.capped,
        uv=_axis(v.uv, detailed=detailed) if v.uv is not None else None,
    )


def _axis(av: AxisVerdict, *, detailed: bool) -> AxisOut:
    return AxisOut(
        grade=_grade(av.grade), note=av.note, derived=dict(av.derived),
        basis=[_basis(b) for b in av.basis] if detailed else [],
    )


def _basis(item: Measurement | State) -> BasisValue | BasisState:
    if isinstance(item, State):
        return BasisState(kind=item.kind.value, category=item.category, area=item.area,
                          valid_from=item.valid_from, valid_to=item.valid_to,
                          issued_at=item.issued_at)
    value, note = _value(item)
    return BasisValue(quantity=item.quantity.value, value=value, unit_note=note,
                      grade=item.grade, source=item.source.value,
                      spatial_ref=item.spatial_ref,
                      valid_at=item.valid_at, issued_at=item.issued_at)


def _value(m: Measurement) -> tuple[float | str, str | None]:
    """②-d-1 의 세 표현을 JSON 으로. **구간을 점으로 누르지 않는다.**

    `'1.0mm 미만'` 을 `0.5` 로 바꾸면 없는 정밀도를 만든다 — 라벨을 그대로 내보내고
    경계값을 `unit_note` 에 붙인다.
    """
    if isinstance(m.value, Interval):
        return m.value.label, f"{m.value.lo}~{m.value.hi}"
    if isinstance(m.value, Code):
        return m.value.name, f"원값 {m.value.raw}"
    return float(m.value), None


def _notes(obs: Observations) -> list[str]:
    """사용자가 알아야 할 한계. **지어내지 않고, 실제로 발생한 것만 적는다.**"""
    out: list[str] = []
    if any(s.kind.value == "air_forecast" for s in obs.states):
        out.append("대기질 미래 구간은 권역 일 예보 기준이다 (③-b — 관측값의 지속 가정은 기각했다)")
    if obs.location.aws_station is None:
        out.append("같은 격자 안에 AWS 지점이 없어 기상청 격자 실황을 썼다 (⑤-d 2순위)")
    if obs.location.warning_area is None:
        out.append("특보구역 매핑표가 아직 없어 시도 단위로만 특보를 찾는다 — 구 단위 특보는 놓칠 수 있다")
    stale = [r.provider.value for r in obs.providers if r.stale]
    if stale:
        out.append(f"옛 값으로 채운 출처가 있다: {', '.join(stale)} (⑤-c — 그래서 GOOD 으로 올리지 않는다)")
    return out


__all__ = ["HORIZON_HOURS", "label_for", "walk"]
