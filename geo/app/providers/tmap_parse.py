"""TMAP 보행자 응답 → RouteResult (facilities 집계 + spots 추출).

spots = 반려견 관심 지점만. 방향전환·직진은 네비 영역이라 안 뽑는다.
실측(2026-08-19): 횡단보도=포인트 211~217, 지하보도=LineString facilityType 14/18 구간, 육교=12,
계단=127/129, 경사로=128, 엘리베이터=218. 포인트에 nearPoiName / intersectionName, 구간에 name(도로명).
"""

from dataclasses import dataclass

from app.providers.base import Facilities, LatLng, RouteResult, Spot

ORIGIN_PASSAGE_WITHIN_M = 150   # 이 안에서 시작하고
ORIGIN_PASSAGE_MAX_M = 120      # 이보다 짧은 지하 구간은 출발 통로(역 출구 등)로 본다

CROSS_TT = {211, 212, 213, 214, 215, 216, 217}
RUN_KIND = {"14": "underpass", "18": "underpass", "12": "overpass"}


def road_rank(name: str) -> int:
    """대로 2 · 로 1 · 그 외 0. 횡단 난이도 근사."""
    if not name:
        return 0
    if name.endswith("대로"):
        return 2
    if name.endswith("로") and not name.endswith("보행자도로"):
        return 1
    return 0


@dataclass
class _Run:
    kind: str
    m: int
    off: int
    at: LatLng


def parse_tmap(data: dict) -> RouteResult:
    feats = data.get("features") or []
    total_d = total_t = 0
    cross = stairs = elev = slope = 0
    pts: list[LatLng] = []
    spots: list[Spot] = []
    runs: list[_Run] = []
    prev_kind: str | None = None
    walked = 0
    last_road = ""            # 직전 구간 도로명 (횡단보도 판정용)
    big_m = 0                 # 큰길(대로/로) 구간 길이 합
    big_cross = 0

    def _pt(g: dict) -> LatLng | None:
        c = g.get("coordinates")
        if not c:
            return None
        if isinstance(c[0], list):
            c = c[0]
        return LatLng(lat=float(c[1]), lng=float(c[0]))

    # 다음 LineString 도로명을 미리 보기 위해 인덱스로 순회
    for i, f in enumerate(feats):
        p = f.get("properties", {})
        g = f.get("geometry", {})
        if "totalDistance" in p:
            total_d, total_t = int(p["totalDistance"]), int(p["totalTime"])
        at = _pt(g)

        if g.get("type") == "Point":
            tt = p.get("turnType")
            near = (p.get("nearPoiName") or "").strip()
            xing = (p.get("intersectionName") or "").strip()
            landmark = near or xing
            # 다음 '도로명이 있는' 구간을 미리 본다 (횡단보도 구간·보행자도로는 건너뜀)
            next_road = ""
            for nf in feats[i + 1:i + 5]:
                np_ = nf.get("properties", {})
                if nf.get("geometry", {}).get("type") != "LineString":
                    continue
                nm = (np_.get("name") or "").strip()
                if nm and nm != "보행자도로":
                    next_road = nm
                    break
            road = next_road if road_rank(next_road) >= road_rank(last_road) else last_road

            if tt in CROSS_TT:
                cross += 1
                # TMAP은 '무슨 길을 건너는지'를 안 준다. 앞뒤 도로명이 같으면 그 길을 따라가며 골목을 건너는 것,
                # 다르면 그 길 자체를 건너는 것으로 본다 (사거리에서 꺾으며 건너면 오판 가능 — 한계).
                along = bool(last_road) and last_road == next_road
                big = road_rank(road) >= 1 and (not along or road_rank(road) >= 2)
                where = f"{landmark} 앞 " if landmark else ""
                if road and along:
                    text = f"{where}{road} 변 골목 횡단보도"
                elif road:
                    text = f"{where}{road} 횡단보도"
                else:
                    text = f"{where}횡단보도"
                if big:
                    big_cross += 1
                if at:
                    spots.append(Spot("crosswalk", at, walked, text, landmark, road, big))
            elif tt in (127, 129):
                stairs += 1
                if at:
                    spots.append(Spot("stairs", at, walked, (f"{landmark} 계단" if landmark else "계단"), landmark, road))
            elif tt == 128:
                slope += 1
                if at:
                    spots.append(Spot("slope", at, walked, "경사로", landmark, road))
            elif tt == 218:
                elev += 1
                if at:
                    spots.append(Spot("elevator", at, walked, "엘리베이터", landmark, road))
            elif tt == 201 and at:
                where = xing or near
                if where in ("도착", "출발", "목적지"):   # TMAP이 endName을 nearPoiName에 넣는다
                    where = xing if xing not in ("도착", "출발", "목적지") else ""
                spots.append(Spot("arrive", at, walked, f"도착 — {where} 근처" if where else "도착", where, last_road))

        elif g.get("type") == "LineString":
            for x, y in g.get("coordinates", []):
                pts.append(LatLng(lat=y, lng=x))
            ft = str(p.get("facilityType", ""))
            kind = RUN_KIND.get(ft)
            m = int(p.get("distance") or 0)
            name = (p.get("name") or "").strip()
            if name and name != "보행자도로":
                last_road = name
            if road_rank(name) >= 1:
                big_m += m
            if kind and kind == prev_kind and runs:
                runs[-1].m += m
            elif kind and at:
                runs.append(_Run(kind, m, walked, at))
            prev_kind = kind
            walked += m

    def is_origin_passage(r: _Run) -> bool:
        return r.kind == "underpass" and r.off <= ORIGIN_PASSAGE_WITHIN_M and r.m < ORIGIN_PASSAGE_MAX_M

    origin_m = 0
    under: list[int] = []
    over: list[int] = []
    for r in runs:
        if is_origin_passage(r):
            origin_m += r.m
            spots.append(Spot("origin_passage", r.at, r.off, f"출발: 지하 통로 {r.m}m (역 출구 등)", length_m=r.m))
        elif r.kind == "underpass":
            under.append(r.m)
            spots.append(Spot("underpass", r.at, r.off, f"지하 통로 {r.m}m", length_m=r.m))
        else:
            over.append(r.m)
            spots.append(Spot("overpass", r.at, r.off, f"육교 {r.m}m", length_m=r.m))

    spots.sort(key=lambda s: s.offset_m)
    return RouteResult(
        mode="walk", distance_m=total_d, duration_s=total_t, source="tmap",
        polyline=tuple(pts),
        facilities=Facilities(crosswalk=cross, stairs=stairs, underpass=len(under), underpass_m=sum(under),
                              origin_passage_m=origin_m, overpass=len(over), elevator=elev, slope=slope,
                              big_road_m=big_m, total_m=walked,
                              big_road_ratio=round(big_m / walked, 2) if walked else 0.0,
                              big_crossings=big_cross),
        spots=tuple(spots),
    )
