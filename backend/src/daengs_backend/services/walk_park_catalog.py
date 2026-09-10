"""Versioned national park point catalog; refresh outside the 45-second entry lease."""

import json
import math
import os
import tempfile
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import unquote

from daengs_backend.services.walk_public_http import PublicSourceError, get_json
from daengs_walk.diary_input import digest

ENDPOINT = "https://api.data.go.kr/openapi/tn_pubr_public_cty_park_info_api"
FORMAT = "public-park-catalog-v1"


def park_row(raw):
    if not isinstance(raw, dict):
        raise TypeError("invalid park row")
    values = {k: raw.get(k) for k in ("manageNo", "parkNm", "parkSe", "referenceDate")}
    if not all(isinstance(v, str) and v.strip() and len(v) <= 200 for v in values.values()):
        raise ValueError("invalid park identity")
    date.fromisoformat(values["referenceDate"])
    lat, lng = float(raw["latitude"]), float(raw["longitude"])
    if not 33 <= lat <= 39.5 or not 124 <= lng <= 132:
        raise ValueError("park coordinate outside Korea")
    return {**values, "latitude": lat, "longitude": lng}


def parse_page(body):
    if not isinstance(body, dict):
        raise PublicSourceError("invalid_park_page")
    root = body.get("response", body)
    if not isinstance(root, dict):
        raise PublicSourceError("invalid_park_page")
    code = str(root["header"]["resultCode"])
    if code == "03":
        return [], 0
    if code != "00":
        raise PublicSourceError("park_api_rejected", code in {"01", "02", "22"})
    page = root["body"]
    items = page["items"]
    if isinstance(items, dict):
        items = items.get("item", [])
    if not isinstance(items, list) or len(items) > 1000:
        raise PublicSourceError("invalid_park_page")
    total = int(page["totalCount"])
    if not 0 <= total <= 60_000:
        raise PublicSourceError("park_catalog_limit")
    return items, total


async def refresh_catalog(transport, key, path):
    """At most 60 calls; publish atomically only after every page has been received."""
    total, raw_rows, receipts = None, [], []
    for page in range(1, 61):
        body = await get_json(
            transport,
            ENDPOINT,
            {
                "serviceKey": unquote(key.strip()),
                "type": "json",
                "pageNo": page,
                "numOfRows": 1000,
            },
        )
        rows, count = parse_page(body)
        if total is None:
            total = count
        if count != total or (not rows and len(raw_rows) < total):
            raise PublicSourceError("park_catalog_changed_or_incomplete")
        raw_rows.extend(rows)
        receipts.append(digest(body))
        if len(raw_rows) >= total:
            break
    if total is None or len(raw_rows) != total:
        raise PublicSourceError("park_catalog_incomplete")
    by_id, conflicts, rejected_reasons = {}, set(), Counter()
    for raw in raw_rows:
        try:
            park = park_row(raw)
        except (ValueError, KeyError, TypeError, OverflowError):
            rejected_reasons["invalid_row"] += 1
            continue
        identity = park["manageNo"]
        if identity in conflicts:
            rejected_reasons["conflicting_identity"] += 1
        elif identity in by_id:
            if park == by_id[identity]:
                rejected_reasons["duplicate_row"] += 1
            else:
                del by_id[identity]
                conflicts.add(identity)
                rejected_reasons["conflicting_identity"] += 2
        else:
            by_id[identity] = park
    parks = list(by_id.values())
    rejected = sum(rejected_reasons.values())
    if not parks:
        raise PublicSourceError("no_valid_parks")
    value = {
        "format": FORMAT,
        "retrieved_at": datetime.now(UTC).isoformat(),
        "total": total,
        "rejected_rows": rejected,
        "pages": receipts,
        "rejected_reasons": dict(rejected_reasons),
        "parks": parks,
        "parks_sha256": digest(parks),
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Named unique temp file also makes concurrent refreshes safe from truncated readers.
    fd, temporary = tempfile.mkstemp(prefix=target.name, suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return value


def read_catalog(path):
    target = Path(path)
    if target.stat().st_size > 25_000_000:
        raise ValueError("catalog too large")
    value = json.loads(target.read_text(encoding="utf-8"))
    age = (datetime.now(UTC) - datetime.fromisoformat(value["retrieved_at"])).total_seconds()
    if value["format"] != FORMAT or not 0 <= age <= 30 * 86400:
        raise ValueError("catalog stale")
    if not isinstance(value["parks"], list) or not 0 < len(value["parks"]) <= 60_000:
        raise ValueError("invalid catalog size")
    if value["parks_sha256"] != digest(value["parks"]):
        raise ValueError("catalog hash mismatch")
    if value["total"] != len(value["parks"]) + value["rejected_rows"]:
        raise ValueError("catalog count mismatch")
    return value


def distance_m(point, park):
    lat1, lat2 = math.radians(point["lat"]), math.radians(park["latitude"])
    dlat = lat2 - lat1
    dlng = math.radians(park["longitude"] - point["lng"])
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 6371000 * 2 * math.asin(min(1, math.sqrt(a)))


def nearby_parks(catalog, point):
    found = []
    for raw in catalog["parks"]:
        park = park_row(raw)
        distance = distance_m(point, park)
        if distance <= 250:
            found.append({**park, "distance_m": round(distance, 1)})
    found.sort(key=lambda p: (p["distance_m"], p["manageNo"]))
    partial = catalog["rejected_rows"] > 0 or len(found) > 10
    return found[:10], partial
