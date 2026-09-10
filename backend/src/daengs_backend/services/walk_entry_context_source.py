"""Bounded structured Place evidence. Never infer a visit or rewrite the user's text."""

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from daengs_backend.config import settings

KINDS = ("leisure", "cafe", "restaurant")


@dataclass(frozen=True)
class Collected:
    status: str
    reason: str | None = None
    payload: dict | None = None
    retrieved_at: str | None = None
    retryable: bool = False
    provider: str | None = None
    operation: str | None = None


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def projection(body):
    groups = body["groups"]
    if not isinstance(groups, list) or len(groups) != len(KINDS):
        raise ValueError("invalid_groups")
    items, seen, partial = [], set(), False
    for group in groups:
        kind = group["kind"]
        if kind not in KINDS or kind in seen or not isinstance(group["results"], list):
            raise ValueError("invalid_group")
        seen.add(kind)
        partial |= bool(group.get("truncated")) or len(group["results"]) > 10
        for hit in group["results"][:10]:
            place = hit["place"]
            distance = float(place["distance_m"])
            if not math.isfinite(distance) or not 0 <= distance <= 250:
                partial = True
                continue
            key = place["key"]
            if not all(
                isinstance(v, str) and 0 < len(v) <= 200
                for v in (place["name"], key["source"], key["ref"])
            ):
                raise ValueError("invalid_place")
            items.append(
                {
                    "kind": kind,
                    "place": {
                        "key": {"source": key["source"], "ref": key["ref"]},
                        "name": place["name"],
                        "distance_m": distance,
                    },
                }
            )
    return Collected(
        "partial" if partial else "known" if items else "empty",
        "bounded_results" if partial else None,
        {
            "items": items,
            "kinds": list(KINDS),
            "radius_m": 250,
            "geometry": "registered_location",
            "visit_confirmed": False,
            "coverage": "selected_place_categories_only",
            "response_sha256": digest(body),
        },
    )


async def collect(tag, content, *, client=None):
    pin = content.get("pin")
    if pin and pin["state"] == "provisional":
        return Collected("not_requested", "pin_not_final")
    point = pin.get("point") if pin else content.get("location")
    if point is None:
        return Collected("not_requested", "no_location")
    if tag in {"space.address", "space.park", "space.commerce", "space.river"}:
        from daengs_backend.services.walk_public_context import collect_public

        return await collect_public(tag, point, pin)
    if tag != "space.facility":
        return Collected("not_requested", "provider_not_connected")
    if client is None:
        async with httpx.AsyncClient(timeout=3.0, follow_redirects=False) as opened:
            return await collect(tag, content, client=opened)
    captured = datetime.now(UTC).isoformat()
    try:
        response = await client.post(
            settings.place_search_base_url.rstrip("/") + "/v2/places/search",
            json={
                "lat": point["lat"],
                "lng": point["lng"],
                "radius_m": 250,
                "kinds": list(KINDS),
                "limit_per_kind": 10,
            },
            timeout=3.0,
        )
        response.raise_for_status()
        value = projection(response.json())
        if pin and value.payload is not None:
            value.payload["location_basis"] = pin["method"]
            value.payload["uncertainty_m"] = pin.get("uncertainty_m")
            value.payload["uncertainty_basis"] = pin.get("uncertainty_basis")
        return Collected(value.status, value.reason, value.payload, captured)
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        return Collected(
            "unavailable",
            f"http_{code}",
            retrieved_at=captured,
            retryable=code == 429 or code >= 500,
        )
    except httpx.RequestError:
        return Collected("unavailable", "transport_error", retrieved_at=captured, retryable=True)
    except (ValueError, KeyError, TypeError, OverflowError):
        return Collected("unavailable", "invalid_response", retrieved_at=captured)
