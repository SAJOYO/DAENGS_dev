"""Registered business composition, without shop visits, opening state or foot traffic."""

import math
from collections import Counter

from daengs_backend.services import walk_area_catalog as catalog
from daengs_backend.services.walk_park_catalog import distance_m
from daengs_backend.services.walk_public_http import PublicSourceError

ENDPOINT = "https://apis.data.go.kr/B553077/api/open/sdsc2/storeListInRadius"


def shop(raw):
    lat, lng = float(raw["lat"]), float(raw["lon"])
    if not 33 <= lat <= 39.5 or not 124 <= lng <= 132:
        raise ValueError("invalid shop coordinate")
    return {
        "id": catalog.label(raw["bizesId"]),
        "latitude": lat,
        "longitude": lng,
        "category_code": catalog.label(raw["indsMclsCd"]),
        "category": catalog.label(raw["indsMclsNm"]),
    }


async def refresh(transport, key, path, point, radius):
    region = catalog.area(point, radius)
    raw, receipts = await catalog.pages(
        transport,
        ENDPOINT,
        key,
        {
            "cx": point["lng"],
            "cy": point["lat"],
            "radius": radius,
        },
    )
    rows, rejected = catalog.unique_rows(raw, shop)
    return catalog.publish(path, "commerce", region, rows, rejected=rejected, receipts=receipts)


def nearby(value, point):
    if not catalog.covers(value, point, 125):
        raise PublicSourceError("outside_catalog_coverage")
    selected = []
    for row in value["rows"]:
        if not all(math.isfinite(row[k]) for k in ("latitude", "longitude")):
            raise ValueError("invalid shop coordinate")
        if distance_m(point, row) <= 125:
            selected.append(row)
    counts = Counter((r["category_code"], r["category"]) for r in selected)
    categories = [
        {"code": code, "name": name, "count": count}
        for (code, name), count in sorted(counts.items(), key=lambda p: (-p[1], p[0]))[:5]
    ]
    return {
        "format": "public-commerce-nearby-v1",
        "query_point": point,
        "radius_m": 125,
        "registered_count": len(selected),
        "categories": categories,
        "other_count": len(selected) - sum(c["count"] for c in categories),
        "coverage": "catalog_registered_businesses",
        "catalog_sha256": value["sha256"],
        "catalog_area": value["area"],
        "complete": value["rejected_rows"] == 0,
        "reference": "registered_business_composition",
        "relation": "registration_only_not_visit_open_or_crowding",
    }
