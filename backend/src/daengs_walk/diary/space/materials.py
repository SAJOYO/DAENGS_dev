"""Saved commerce/park/land responses -> finite, scoped spatial candidates.

No network, LLM, dog action, scene selection, capacity or eviction. The caller
supplies query footprints. A footprint is not a claim of visiting every place.
"""

import math
from collections import Counter
from copy import deepcopy
from typing import Literal

from pydantic import Field, JsonValue, model_validator

from daengs_walk.diary.contracts.input import DiaryContract as Contract
from daengs_walk.diary.contracts.input import Point
from daengs_walk.diary.contracts.input import digest as payload_hash
from daengs_walk.diary.space.cases import CASES, COMMERCE_GROUPS, LAND_TYPES, PARK_TYPES
from daengs_walk.diary.space.geometry import distance, feature_contains, xy

_REASONS = {
    "unknown_commerce_group",
    "unknown_land_code",
    "inconsistent_land_codes",
    "invalid_label",
    "outside_source_coverage",
    "invalid_coordinate_type",
    "provider_not_successful",
    "invalid_page_sequence",
    "invalid_page_items",
    "missing_or_inconsistent_pages",
    "inconsistent_commerce_month",
    "invalid_park_area",
    "invalid_land_response",
    "unsupported_land_crs",
    "unsupported_land_geometry",
    "empty_land_geometry",
    "invalid_land_ring",
    "invalid_land_coordinate",
    "invalid_land_polygon",
}


def _reason(error, fallback):
    return str(error) if str(error) in _REASONS else fallback


class CommercePolicy(Contract):
    """Explicit initial classification thresholds, not calibrated product policy."""

    sparse_max_shops: int = Field(default=5, ge=0)
    dominant_min_shops: int = Field(default=10, ge=1)
    dominant_share: float = Field(default=0.4, gt=0, le=1)
    dominant_gap: float = Field(default=0.15, ge=0, le=1)
    cluster_rms_radius_ratio: float = Field(default=0.25, gt=0, le=1)


class AreaInput(Contract):
    query_point: Point
    radius_m: float = Field(gt=0, le=3000)
    pages: tuple[dict[str, JsonValue], ...]


class LandInput(Contract):
    query_point: Point
    layer: str = Field(pattern=r"^EGIS:lv3_[a-zA-Z0-9_-]+$")
    response: dict[str, JsonValue]


class SpaceInput(Contract):
    point: Point
    commerce: AreaInput | None = None
    park: AreaInput | None = None
    land_cover: LandInput | None = None
    commerce_policy: CommercePolicy = Field(default_factory=CommercePolicy)

    @model_validator(mode="after")
    def same_query(self):
        for source in (self.commerce, self.park, self.land_cover):
            if source and source.query_point != self.point:
                raise ValueError("source query differs from the normalization point")
        return self


class SpaceMaterial(Contract):
    id: str
    source: Literal["commerce", "park", "land_cover"]
    case_ids: tuple[str, ...]
    material: dict[str, str]
    scope: dict[str, JsonValue]
    support: dict[str, JsonValue]

    @model_validator(mode="after")
    def finite_material(self):
        if not self.case_ids or len(self.case_ids) != len(set(self.case_ids)):
            raise ValueError("material needs distinct predefined cases")
        expected = {}
        for case in self.case_ids:
            if case not in CASES or not case.startswith(self.source + ":"):
                raise ValueError("material case is not in this source dictionary")
            if set(expected) & set(CASES[case]):
                raise ValueError("cases cannot overwrite each other's meaning")
            expected.update(CASES[case])
        if self.source == "park":
            expected["공원명"] = _label(self.material.get("공원명"))
        if self.material != expected:
            raise ValueError("material differs from the predefined dictionary")
        return self


class SpaceMaterials(Contract):
    format: Literal["space-materials-v1"] = "space-materials-v1"
    dictionary_version: str = Field(default_factory=lambda: payload_hash(CASES))
    point: Point
    policy: CommercePolicy
    materials: tuple[SpaceMaterial, ...]
    audit: tuple[dict[str, JsonValue], ...]


def _material(source, cases, scope, support, **names):
    value = dict(names)
    for case in cases:
        value.update(CASES[case])
    data = deepcopy(
        {"source": source, "case_ids": cases, "material": value, "scope": scope, "support": support}
    )
    return SpaceMaterial(id="space:" + payload_hash(data), **data)


def _pages(pages, source):
    rows, seen, totals = [], set(), set()
    for page in pages:
        header, body = page["header"], page["body"]
        if header["resultCode"] != "00":
            raise ValueError("provider_not_successful")
        if any(
            not (type(body[k]) is int or isinstance(body[k], str) and body[k].isdigit())
            for k in ("pageNo", "totalCount")
        ):
            raise ValueError("invalid_page_sequence")
        number, total = int(body["pageNo"]), int(body["totalCount"])
        if number < 1 or number in seen or total < 0:
            raise ValueError("invalid_page_sequence")
        seen.add(number)
        totals.add(total)
        items = body["items"]
        if source == "park":
            items = items["item"]
        if not isinstance(items, list):
            raise TypeError("invalid_page_items")
        rows.extend(items)
    if len(totals) != 1:
        raise ValueError("missing_or_inconsistent_pages")
    complete = len(rows) == next(iter(totals)) and seen == set(range(1, len(seen) + 1))
    return rows, complete


def _point(lat, lng):
    if isinstance(lat, bool) or isinstance(lng, bool):
        raise TypeError("invalid_coordinate_type")
    point = Point(lat=lat, lng=lng)
    if not 33 <= point.lat <= 39.5 or not 124 <= point.lng <= 132:
        raise ValueError("outside_source_coverage")
    return point.model_dump()


def _label(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError("invalid_label")
    return value.strip()


def _unique(rows, source, parse, audit):
    valid, conflicts, rejected = {}, set(), Counter()
    for row in rows:
        try:
            key, value = parse(row)
            if key in conflicts:
                continue
            if key in valid and valid[key] != value:
                del valid[key]
                conflicts.add(key)
            else:
                valid[key] = value
        except (KeyError, ValueError, TypeError, OverflowError) as error:
            rejected[_reason(error, "invalid_row")] += 1
    if rejected or conflicts:
        audit.append(
            {
                "source": source,
                "reason": "invalid_or_conflicting_rows",
                "rejections": dict(sorted(rejected.items())),
                "conflicting_ids": sorted(conflicts),
            }
        )
    return dict(sorted(valid.items())), not (rejected or conflicts)


def _commerce(area, policy, audit):
    rows, complete = _pages(area.pages, "commerce")

    def parse(row):
        major, middle = _label(row["indsLclsCd"]), _label(row["indsMclsCd"])
        group = COMMERCE_GROUPS.get(major)
        if group is None or not middle.startswith(major):
            raise ValueError("unknown_commerce_group")
        return _label(row["bizesId"]), {
            "point": _point(row["lat"], row["lon"]),
            "group": group,
            "beverage": major == "I2" and middle == "I212",
        }

    shops, valid = _unique(rows, "commerce", parse, audit)
    if not complete or not valid:
        audit.append({"source": "commerce", "reason": "composition_coverage_incomplete"})
        return []
    point = area.query_point.model_dump()
    unique_count = len(shops)
    shops = {k: v for k, v in shops.items() if distance(v["point"], point) <= area.radius_m}
    if not shops:
        audit.append({"source": "commerce", "reason": "no_registered_shops_in_footprint"})
        return []
    counts = Counter(v["group"] for v in shops.values())
    beverage_count = sum(v["beverage"] for v in shops.values())
    # Distinct registered coordinates prevent many tenants in one building from
    # changing geometric spread. Composition still counts distinct businesses.
    sites = sorted({tuple(xy(v["point"], point)) for v in shops.values()})
    centre = tuple(sum(p[i] for p in sites) / len(sites) for i in (0, 1))
    rms = math.sqrt(sum(math.dist(p, centre) ** 2 for p in sites) / len(sites))
    distribution = (
        "sparse"
        if len(shops) <= policy.sparse_max_shops
        else "clustered"
        if rms / area.radius_m <= policy.cluster_rms_radius_ratio
        else "scattered"
    )
    cases = ["commerce:" + distribution]
    if len(shops) >= policy.dominant_min_shops:
        ranked = sorted(counts.items(), key=lambda p: (-p[1], p[0]))
        first, second = ranked[0][1], ranked[1][1] if len(ranked) > 1 else 0
        dominant = (
            ranked[0][0]
            if first / len(shops) >= policy.dominant_share
            and (first - second) / len(shops) >= policy.dominant_gap
            else "mixed"
        )
        if (
            dominant == "food"
            and beverage_count / len(shops) >= policy.dominant_share
            and (beverage_count - max(first - beverage_count, second)) / len(shops)
            >= policy.dominant_gap
        ):
            dominant = "beverage"
        cases.append("commerce:" + dominant)
    months = {p["header"].get("stdrYm") for p in area.pages}
    if len(months) > 1:
        raise ValueError("inconsistent_commerce_month")
    return [
        _material(
            "commerce",
            cases,
            {"kind": "query_circle", "point": point, "radius_m": area.radius_m},
            {
                "registered_count": len(shops),
                "response_rows": len(rows),
                "unique_response_shops": unique_count,
                "outside_radius_count": unique_count - len(shops),
                "groups": dict(sorted(counts.items())),
                "registered_sites": len(sites),
                "spread_rms_m": round(rms, 3),
                "nearest_registered_point_m": round(min(math.hypot(*p) for p in sites), 3),
                "centroid_distance_m": round(math.hypot(*centre), 3),
                "source_ids": list(shops),
                "source_hash": payload_hash(shops),
                "reference_month": next(iter(months)),
                "beverage_count": beverage_count,
                "classification_policy": policy.model_dump(),
            },
        )
    ]


def _parks(area, audit):
    rows, complete = _pages(area.pages, "park")
    reference_dates = {}

    def parse(row):
        key = _label(row["manageNo"])
        size = row.get("parkAr")
        if isinstance(size, bool):
            raise TypeError("invalid_park_area")
        size = None if size in (None, "") else float(size)
        if size is not None and (not math.isfinite(size) or size < 0):
            raise ValueError("invalid_park_area")
        value = {
            "name": _label(row["parkNm"]),
            "kind": _label(row["parkSe"]),
            "point": _point(row["latitude"], row["longitude"]),
            "area_m2": size,
        }
        date = row.get("referenceDate")
        if date not in (None, ""):
            reference_dates.setdefault(key, set()).add(_label(date))
        return key, value

    parks, valid = _unique(rows, "park", parse, audit)
    point, result = area.query_point.model_dump(), []
    for key, park in parks.items():
        metres = distance(park["point"], point)
        if metres > area.radius_m:
            continue
        kind = park["kind"] if park["kind"] in PARK_TYPES else "unspecified"
        if kind == "unspecified":
            audit.append({"source": "park", "source_id": key, "reason": "unknown_park_type"})
        result.append(
            _material(
                "park",
                ["park:" + kind],
                {
                    "kind": "registered_point",
                    "point": park["point"],
                    "query_point": point,
                    "distance_m": round(metres, 3),
                    "query_radius_m": area.radius_m,
                },
                {
                    "source_id": key,
                    "source_complete": complete and valid,
                    "area_m2": park["area_m2"],
                    "reference_dates": sorted(reference_dates.get(key, ())),
                },
                공원명=park["name"],
            )
        )
    if not complete:
        audit.append({"source": "park", "reason": "partial_catalog"})
    return result


def _land(land, audit):
    response = land.response
    if response["type"] != "FeatureCollection":
        raise ValueError("invalid_land_response")
    crs = response["crs"]["properties"]["name"]
    point, result = land.query_point.model_dump(), []
    features = response["features"]
    if not isinstance(features, list):
        raise TypeError("invalid_land_response")

    def parse(feature):
        props = feature["properties"]
        code = str(props["l3_code"])
        if code not in LAND_TYPES:
            raise ValueError("unknown_land_code")
        if "l2_code" in props and str(props["l2_code"]) != code[:2] + "0":
            raise ValueError("inconsistent_land_codes")
        return _label(feature["id"]), {
            "code": code,
            "geometry": feature["geometry"],
            "covers_query": feature_contains(feature["geometry"], crs, point),
            "image_date": props.get("img_date"),
        }

    values, _ = _unique(features, "land_cover", parse, audit)
    for key, feature in values.items():
        if not feature["covers_query"]:
            audit.append(
                {"source": "land_cover", "source_id": key, "reason": "feature_does_not_cover_query"}
            )
            continue
        result.append(
            _material(
                "land_cover",
                ["land_cover:" + feature["code"]],
                {
                    "kind": "feature_at_query",
                    "query_point": point,
                    "distance_m": 0,
                    "crs": crs,
                    "geometry": feature["geometry"],
                },
                {"source_id": key, "layer": land.layer, "image_date": feature["image_date"]},
            )
        )
    return result


def normalize_spaces(raw: SpaceInput | dict) -> SpaceMaterials:
    source = SpaceInput.model_validate(raw)
    materials, audit = [], []
    for kind, value in (
        ("commerce", source.commerce),
        ("park", source.park),
        ("land_cover", source.land_cover),
    ):
        if value is None:
            audit.append({"source": kind, "reason": "not_supplied"})
            continue
        try:
            if kind == "commerce":
                materials.extend(_commerce(value, source.commerce_policy, audit))
            elif kind == "park":
                materials.extend(_parks(value, audit))
            else:
                materials.extend(_land(value, audit))
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            audit.append({"source": kind, "reason": _reason(error, "invalid_source_response")})
    return SpaceMaterials(
        point=source.point,
        policy=source.commerce_policy,
        materials=tuple(sorted(materials, key=lambda m: (m.source, m.id))),
        audit=tuple(audit),
    )
