"""Small checked composition/distance dictionaries, never raw shops or river endpoints."""

import math
import re

from pyproj import Transformer

from daengs_walk.diary_background import Projection
from daengs_walk.diary_input import digest
from daengs_walk.diary_public_background import text

METRIC = Transformer.from_crs(4326, 5179, always_xy=True)


def position(point):
    lat, lng = point["lat"], point["lng"]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (lat, lng)):
        raise ValueError("invalid coordinate")
    if not 33 <= lat <= 39.5 or not 124 <= lng <= 132:
        raise ValueError("invalid coordinate")
    return METRIC.transform(lng, lat)


def checksum(value):
    if not isinstance(value, str) or not re.fullmatch("[a-f0-9]{64}", value):
        raise ValueError("invalid snapshot hash")
    return value


def project_area(saved, core, piece):
    p = saved.payload
    commerce = saved.provider == "data-go-kr-commerce"
    schema = "public-commerce-nearby-v1" if commerce else "public-river-nearby-v1"
    radius = 125 if commerce else 250
    if (
        p.get("format") != schema
        or p.get("radius_m") != radius
        or type(p.get("complete")) is not bool
    ):
        raise ValueError("invalid regional schema")
    if (
        saved.status not in {"known", "partial", "empty"}
        or (saved.status == "partial") == p["complete"]
    ):
        raise ValueError("invalid regional completeness")
    area = p["catalog_area"]
    coverage_radius = area["radius_m"]
    if isinstance(coverage_radius, bool) or not 500 <= coverage_radius <= 3000:
        raise ValueError("invalid regional radius")
    at = position(core.anchor.point.model_dump())
    if math.dist(position(area["center"]), at) + radius + 5 > coverage_radius:
        raise ValueError("point outside regional coverage")
    catalog_hash = checksum(p["catalog_sha256"])
    if commerce:
        if (
            p.get("reference") != "registered_business_composition"
            or p.get("relation") != "registration_only_not_visit_open_or_crowding"
            or p.get("coverage") != "catalog_registered_businesses"
            or type(p.get("registered_count")) is not int
            or not 0 <= p["registered_count"] <= 30_000
            or type(p.get("other_count")) is not int
            or not 0 <= p["other_count"] <= p["registered_count"]
            or not isinstance(p.get("categories"), list)
            or len(p["categories"]) > 5
        ):
            raise ValueError("invalid commerce composition")
        categories, seen = [], set()
        for row in p["categories"]:
            code, name, count = text(row["code"]), text(row["name"]), row["count"]
            if code in seen or type(count) is not int or not 0 < count <= p["registered_count"]:
                raise ValueError("invalid commerce category")
            seen.add(code)
            categories.append({"name": name, "count": count})
        if sum(c["count"] for c in categories) + p["other_count"] != p["registered_count"]:
            raise ValueError("commerce counts differ")
        if not categories:
            return Projection(reason="no_valid_features")
        return Projection(
            (
                piece(
                    "space_relation",
                    schema,
                    {
                        "source_ref": {
                            "source": saved.provider,
                            "ref": digest({"catalog": catalog_hash, "point": p["query_point"]}),
                        },
                        "radius_m": radius,
                        "registered_count": p["registered_count"],
                        "categories": categories,
                        "other_count": p["other_count"],
                        "complete": p["complete"],
                        **{k: p[k] for k in ("reference", "relation", "coverage")},
                    },
                ),
            )
        )
    if (
        p.get("geometry") != "egis_river_polygon"
        or p.get("geometry_crs") != "EPSG:5179"
        or p.get("geometry_reference_date") is not None
        or p.get("coverage") != "egis_catalog_geometry"
        or p.get("relation") != "geometry_distance_not_bank_path_or_visit"
        or not isinstance(p.get("items"), list)
        or len(p["items"]) > 3
    ):
        raise ValueError("invalid river relation")
    pieces, rejected, seen = [], [], set()
    for index, row in enumerate(p["items"]):
        try:
            identifier, name = text(row["id"]), text(row["name"])
            distance = row["distance_m"]
            if (
                identifier in seen
                or isinstance(distance, bool)
                or not isinstance(distance, (float, int))
                or not 0 <= distance <= 250
            ):
                raise ValueError("invalid river distance")
            nearest = position(row["nearest_point"])
            if abs(distance - math.dist(at, nearest)) > 0.11:
                raise ValueError("river distance differs from saved geometry point")
            checksum(row["geometry_sha256"])
            seen.add(identifier)
            pieces.append(
                piece(
                    "space_relation",
                    schema,
                    {
                        "source_ref": {"source": saved.provider, "ref": identifier},
                        "name": name,
                        "distance_m": distance,
                        "reference": "egis_river_polygon",
                        "relation": p["relation"],
                        "coverage": p["coverage"],
                        "complete": p["complete"],
                        "geometry_reference_date": None,
                    },
                )
            )
        except (KeyError, ValueError, TypeError):
            rejected.append(index)
    return Projection(tuple(pieces), None if pieces else "no_valid_features", tuple(rejected))
