from dataclasses import dataclass

from app.providers.base import Facilities, LatLng, RouteResult, Spot

ORIGIN_PASSAGE_WITHIN_M = 150
ORIGIN_PASSAGE_MAX_M = 120
CROSS_TT = {211, 212, 213, 214, 215, 216, 217}
RUN_KIND = {"14": "underpass", "18": "underpass", "12": "overpass"}


def road_rank(name: str) -> int:
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
    """Convert the original TMAP response into route facts and dog-interest spots."""

    features = data.get("features") or []
    total_distance = total_time = 0
    crosswalks = stairs = elevators = slopes = 0
    points: list[LatLng] = []
    spots: list[Spot] = []
    runs: list[_Run] = []
    previous_kind: str | None = None
    walked = 0
    last_road = ""
    big_road_m = 0
    big_crossings = 0

    def point(geometry: dict) -> LatLng | None:
        coordinates = geometry.get("coordinates")
        if not coordinates:
            return None
        if isinstance(coordinates[0], list):
            coordinates = coordinates[0]
        return LatLng(lat=float(coordinates[1]), lng=float(coordinates[0]))

    for index, feature in enumerate(features):
        properties = feature.get("properties", {})
        geometry = feature.get("geometry", {})
        if "totalDistance" in properties:
            total_distance = int(properties["totalDistance"])
            total_time = int(properties["totalTime"])
        at = point(geometry)

        if geometry.get("type") == "Point":
            turn_type = properties.get("turnType")
            near = (properties.get("nearPoiName") or "").strip()
            intersection = (properties.get("intersectionName") or "").strip()
            landmark = near or intersection
            next_road = ""
            for next_feature in features[index + 1 : index + 5]:
                next_properties = next_feature.get("properties", {})
                if next_feature.get("geometry", {}).get("type") != "LineString":
                    continue
                name = (next_properties.get("name") or "").strip()
                if name and name != "보행자도로":
                    next_road = name
                    break
            road = next_road if road_rank(next_road) >= road_rank(last_road) else last_road

            if turn_type in CROSS_TT:
                crosswalks += 1
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
                    big_crossings += 1
                if at:
                    spots.append(Spot("crosswalk", at, walked, text, landmark, road, big))
            elif turn_type in (127, 129):
                stairs += 1
                if at:
                    spots.append(
                        Spot(
                            "stairs",
                            at,
                            walked,
                            f"{landmark} 계단" if landmark else "계단",
                            landmark,
                            road,
                        )
                    )
            elif turn_type == 128:
                slopes += 1
                if at:
                    spots.append(Spot("slope", at, walked, "경사로", landmark, road))
            elif turn_type == 218:
                elevators += 1
                if at:
                    spots.append(Spot("elevator", at, walked, "엘리베이터", landmark, road))
            elif turn_type == 201 and at:
                where = intersection or near
                if where in ("도착", "출발", "목적지"):
                    where = intersection if intersection not in ("도착", "출발", "목적지") else ""
                text = f"도착 — {where} 근처" if where else "도착"
                spots.append(Spot("arrive", at, walked, text, where, last_road))

        elif geometry.get("type") == "LineString":
            for longitude, latitude in geometry.get("coordinates", []):
                points.append(LatLng(lat=latitude, lng=longitude))
            facility_type = str(properties.get("facilityType", ""))
            kind = RUN_KIND.get(facility_type)
            distance = int(properties.get("distance") or 0)
            name = (properties.get("name") or "").strip()
            if name and name != "보행자도로":
                last_road = name
            if road_rank(name) >= 1:
                big_road_m += distance
            if kind and kind == previous_kind and runs:
                runs[-1].m += distance
            elif kind and at:
                runs.append(_Run(kind, distance, walked, at))
            previous_kind = kind
            walked += distance

    def is_origin_passage(run: _Run) -> bool:
        return (
            run.kind == "underpass"
            and run.off <= ORIGIN_PASSAGE_WITHIN_M
            and run.m < ORIGIN_PASSAGE_MAX_M
        )

    origin_passage_m = 0
    underpasses: list[int] = []
    overpasses: list[int] = []
    for run in runs:
        if is_origin_passage(run):
            origin_passage_m += run.m
            spots.append(
                Spot(
                    "origin_passage",
                    run.at,
                    run.off,
                    f"출발: 지하 통로 {run.m}m (역 출구 등)",
                    length_m=run.m,
                )
            )
        elif run.kind == "underpass":
            underpasses.append(run.m)
            spots.append(
                Spot(
                    "underpass",
                    run.at,
                    run.off,
                    f"지하 통로 {run.m}m",
                    length_m=run.m,
                )
            )
        else:
            overpasses.append(run.m)
            spots.append(
                Spot(
                    "overpass",
                    run.at,
                    run.off,
                    f"육교 {run.m}m",
                    length_m=run.m,
                )
            )

    spots.sort(key=lambda item: item.offset_m)
    return RouteResult(
        mode="walk",
        distance_m=total_distance,
        duration_s=total_time,
        source="tmap",
        polyline=tuple(points),
        facilities=Facilities(
            crosswalk=crosswalks,
            stairs=stairs,
            underpass=len(underpasses),
            underpass_m=sum(underpasses),
            origin_passage_m=origin_passage_m,
            overpass=len(overpasses),
            elevator=elevators,
            slope=slopes,
            big_road_m=big_road_m,
            total_m=walked,
            big_road_ratio=round(big_road_m / walked, 2) if walked else 0.0,
            big_crossings=big_crossings,
        ),
        spots=tuple(spots),
    )
