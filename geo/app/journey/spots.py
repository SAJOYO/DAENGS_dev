"""spots — 반려견 관심 지점 + 한마디. companion=none이면 도착 앵커만.

**도착 문구는 도메인이 주입한다.** 여기(공용 route 층)가 "진료 전 전화 권장" 같은 걸 알면
산책·약국이 이 층을 못 쓴다. 병원 feature 가 자기 문구를 넘긴다.
"""

DEFAULT_ARRIVE_NOTE = "도착 — 간판·층수 확인"

from app.journey.models import Companion, SpotOut
from app.profile.contract import DogProfile
from app.providers.base import RouteResult, Spot


def spot_note(sp: Spot, profile: DogProfile | None,
              arrive_note: str | None = None) -> tuple[str | None, bool]:
    """(note, warn). 이 개한테 이 지점이 뭔지 한마디. 없으면 None."""
    p = profile
    if sp.kind == "crosswalk":
        bits = []
        if sp.big_road:
            bits.append("큰길 — 목줄 짧게, 신호 기다리기")
        if p and "reactive_to_dogs" in p.temperament:
            bits.append("건널목에 다른 개 있을 수 있음")
        if p and p.is_senior and sp.big_road:
            bits.append("신호 한 번에 못 건너면 중앙 대기")
        return (" · ".join(bits) or None, sp.big_road)
    if sp.kind == "stairs":
        if p and (p.is_senior or p.has_joint_issue):
            return ("계단 — 안고 이동 권장", True)
        if p and p.size_class == "small":
            return ("계단 — 소형견은 안고 이동", True)
        return ("계단", False)
    if sp.kind == "underpass":
        if p and p.size_class == "large":
            return (f"지하 통로 {sp.length_m}m — 대형견 스트레스, 짧게 통과", True)
        if p and "timid" in p.temperament:
            return (f"지하 통로 {sp.length_m}m — 소음·울림, 겁 많으면 주의", True)
        return (f"지하 통로 {sp.length_m}m", sp.length_m >= 100)
    if sp.kind == "overpass":
        return ("육교 — 계단 있을 가능성", bool(p and (p.is_senior or p.has_joint_issue)))
    if sp.kind == "elevator":
        return ("엘리베이터 — 케이지/안고 탑승", False)
    if sp.kind == "slope":
        return ("경사로", bool(p and p.has_joint_issue))
    if sp.kind == "origin_passage":
        return ("출발 지점 통로 (이미 서 있는 곳)", False)
    if sp.kind == "arrive":
        return (arrive_note or DEFAULT_ARRIVE_NOTE, False)
    return (None, False)


def spots_out(r: RouteResult, profile: DogProfile | None, companion: Companion,
              arrive_note: str | None = None) -> list[SpotOut]:
    """같은 (종류, 도로)의 노트는 첫 번째만 풀로. companion=none이면 도착 앵커만."""
    out: list[SpotOut] = []
    seen: set[tuple[str, str]] = set()
    for sp in r.spots:
        if companion == "none" and sp.kind != "arrive":
            continue
        note, warn = spot_note(sp, profile, arrive_note) if companion == "dog" else (None, False)
        key = (sp.kind, sp.road)
        if note and key in seen and sp.kind == "crosswalk":
            note = "큰길" if sp.big_road else None
        seen.add(key)
        out.append(SpotOut(kind=sp.kind, lat=sp.at.lat, lng=sp.at.lng, offset_m=sp.offset_m, text=sp.text,
                           landmark=sp.landmark, road=sp.road, big_road=sp.big_road, length_m=sp.length_m,
                           note=note, warn=warn))
    return out
