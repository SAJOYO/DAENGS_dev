from daengs_journey.journey.contract import Companion
from daengs_journey.journey.models import SpotOut
from daengs_journey.providers.base import RouteResult, Spot

DEFAULT_ARRIVE_NOTE = "도착 — 간판·층수 확인"


def spot_note(spot: Spot, arrive_note: str | None = None) -> tuple[str | None, bool]:
    """The generic notes used by DAENGS_geo when no profile is resolved."""

    if spot.kind == "crosswalk":
        return ("큰길 — 목줄 짧게, 신호 기다리기" if spot.big_road else None, spot.big_road)
    if spot.kind == "stairs":
        return ("계단", False)
    if spot.kind == "underpass":
        return (f"지하 통로 {spot.length_m}m", spot.length_m >= 100)
    if spot.kind == "overpass":
        return ("육교 — 계단 있을 가능성", False)
    if spot.kind == "elevator":
        return ("엘리베이터 — 케이지/안고 탑승", False)
    if spot.kind == "slope":
        return ("경사로", False)
    if spot.kind == "origin_passage":
        return ("출발 지점 통로 (이미 서 있는 곳)", False)
    if spot.kind == "arrive":
        return (arrive_note or DEFAULT_ARRIVE_NOTE, False)
    return (None, False)


def spots_out(
    route: RouteResult,
    companion: Companion,
    arrive_note: str | None = None,
) -> list[SpotOut]:
    output: list[SpotOut] = []
    seen: set[tuple[str, str]] = set()
    for spot in route.spots:
        if companion == "none" and spot.kind != "arrive":
            continue
        note, warn = spot_note(spot, arrive_note) if companion == "dog" else (None, False)
        key = (spot.kind, spot.road)
        if note and key in seen and spot.kind == "crosswalk":
            note = "큰길" if spot.big_road else None
        seen.add(key)
        output.append(
            SpotOut(
                kind=spot.kind,
                lat=spot.at.lat,
                lng=spot.at.lng,
                offset_m=spot.offset_m,
                text=spot.text,
                landmark=spot.landmark,
                road=spot.road,
                big_road=spot.big_road,
                length_m=spot.length_m,
                note=note,
                warn=warn,
            )
        )
    return output
