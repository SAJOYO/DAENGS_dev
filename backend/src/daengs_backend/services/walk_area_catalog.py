"""Bounded regional snapshots; only explicit CLI refreshes use the network."""

import json
import math
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote

from pyproj import Transformer

from daengs_backend.services.walk_public_http import PublicSourceError, get_json
from daengs_walk.diary_input import digest

FORWARD = Transformer.from_crs(4326, 5179, always_xy=True)
REVERSE = Transformer.from_crs(5179, 4326, always_xy=True)


def label(value):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 200
        or value.lower() == "null"
    ):
        raise ValueError("invalid public label")
    return value.strip()


def area(point, radius_m):
    lat, lng = point["lat"], point["lng"]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (lat, lng, radius_m)):
        raise ValueError("invalid area")
    if not 33 <= lat <= 39.5 or not 124 <= lng <= 132 or not 500 <= radius_m <= 3000:
        raise ValueError("area outside supported range")
    return {"center": {"lat": lat, "lng": lng}, "radius_m": radius_m}


def xy(point):
    return FORWARD.transform(point["lng"], point["lat"])


def covers(catalog, point, radius):
    # 5m guard for the provider's geodesic circle versus the local metric projection.
    return (
        math.dist(xy(catalog["area"]["center"]), xy(point)) + radius + 5
        <= catalog["area"]["radius_m"]
    )


async def pages(transport, endpoint, key, query, *, max_pages=30, identity_field=None):
    """No partial publication on a missing/repeated/changed page or exhausted budget."""
    result, hashes, total = [], [], None
    seen_pages, seen_ids = set(), set()
    for number in range(1, max_pages + 1):
        body = await get_json(
            transport,
            endpoint,
            {
                **query,
                "serviceKey": unquote(key.strip()),
                "type": "json",
                "numOfRows": 1000,
                "pageNo": number,
            },
        )
        if not isinstance(body, dict):
            raise PublicSourceError("invalid_public_page")
        root = body.get("response", body)
        if not isinstance(root, dict) or not isinstance(root.get("header"), dict):
            raise PublicSourceError("invalid_public_page")
        code = str(root["header"]["resultCode"])
        if code == "03":
            rows, count = [], 0
        elif code == "00":
            page = root["body"]
            rows = page["items"]
            if isinstance(rows, dict):
                rows = rows.get("item", [])
            count = int(page["totalCount"])
        else:
            raise PublicSourceError("public_api_rejected", code in {"01", "02", "22", "23"})
        if not isinstance(rows, list) or len(rows) > 1000 or not 0 <= count <= max_pages * 1000:
            raise PublicSourceError("catalog_page_limit")
        if total is None:
            total = count
        # Compare a multiset so reordered repeats cannot look like a new page.
        signature = digest(sorted(digest(row) for row in rows))
        page_ids = set()
        if identity_field is not None:
            for row in rows:
                try:
                    page_ids.add(label(row[identity_field]))
                except (KeyError, ValueError, TypeError):
                    continue  # Normalization records invalid rows as incomplete coverage.
        if (
            count != total
            or signature in seen_pages
            or seen_ids & page_ids
            or (not rows and len(result) < total)
        ):
            raise PublicSourceError("catalog_changed_or_incomplete")
        seen_pages.add(signature)
        seen_ids.update(page_ids)
        hashes.append(digest(rows))  # Preserve receipts of the actual ordered response rows.
        result.extend(rows)
        if len(result) == total:
            return result, hashes
        if len(result) > total:
            break
    raise PublicSourceError("catalog_incomplete")


def unique_rows(rows, normalize):
    found, conflicts, rejected = {}, set(), 0
    for raw in rows:
        try:
            row = normalize(raw)
            if row is None:  # Valid geometry whose envelope matched but shape is outside the area.
                continue
            key = row["id"]
        except (KeyError, ValueError, TypeError, OverflowError):
            rejected += 1
            continue
        if key in conflicts:
            rejected += 1
        elif key in found:
            if found[key] != row:
                del found[key]
                conflicts.add(key)
                rejected += 2
            # An identical repeated entity is counted once, not a coverage loss.
        else:
            found[key] = row
    return sorted(found.values(), key=lambda row: row["id"]), rejected


def publish(path, kind, region, rows, *, rejected=0, receipts=None, extra=None):
    value = {
        "format": "walk-area-catalog-v1",
        "kind": kind,
        "area": region,
        "retrieved_at": datetime.now(UTC).isoformat(),
        "rows": rows,
        "rejected_rows": rejected,
        "pages": receipts or [],
        **(extra or {}),
    }
    value["sha256"] = digest(value)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=target.name, suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
        if os.path.getsize(temporary) > 15_000_000:
            raise PublicSourceError("catalog_too_large")
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return value


def read(path, kind):
    target = Path(path)
    if target.stat().st_size > 15_000_000:
        raise ValueError("catalog too large")
    value = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("invalid catalog")
    signature = value.pop("sha256")
    if signature != digest(value):
        raise ValueError("catalog hash mismatch")
    value["sha256"] = signature
    age = (datetime.now(UTC) - datetime.fromisoformat(value["retrieved_at"])).total_seconds()
    if (
        value["format"] != "walk-area-catalog-v1"
        or value["kind"] != kind
        or not 0 <= age <= 30 * 86400
    ):
        raise ValueError("catalog stale or incompatible")
    area(value["area"]["center"], value["area"]["radius_m"])
    if not isinstance(value["rows"], list) or len(value["rows"]) > 30_000:
        raise ValueError("invalid catalog rows")
    if type(value["rejected_rows"]) is not int or value["rejected_rows"] < 0:
        raise ValueError("invalid catalog coverage")
    return value
