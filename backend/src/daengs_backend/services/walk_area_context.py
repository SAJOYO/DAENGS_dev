"""Regional cache -> the same fenced context envelope, with no worker-side API calls."""

import asyncio

from shapely.errors import ShapelyError

from daengs_backend.config import settings
from daengs_backend.services import walk_area_catalog, walk_commerce_catalog, walk_river_catalog
from daengs_backend.services.walk_entry_context_source import Collected
from daengs_backend.services.walk_public_http import PublicSourceError


def snapshot(kind, point):
    path = getattr(settings, f"walk_{kind}_catalog_path")
    value = walk_area_catalog.read(path, kind)
    module = walk_commerce_catalog if kind == "commerce" else walk_river_catalog
    return module.nearby(value, point), value["retrieved_at"]


async def collect_area(tag, point, pin=None):
    if not settings.walk_area_context_enabled:
        return Collected("not_requested", "provider_not_connected")
    kind = "commerce" if tag == "space.commerce" else "river"
    meta = {
        "provider": "data-go-kr-commerce" if kind == "commerce" else "egis-rivers",
        "operation": "registered-business-area-catalog"
        if kind == "commerce"
        else "river-polygon-area-catalog",
    }
    if not getattr(settings, f"walk_{kind}_catalog_path"):
        return Collected("not_requested", "provider_not_configured", **meta)
    point = {"lat": point["lat"], "lng": point["lng"]}
    try:
        payload, retrieved = await asyncio.to_thread(snapshot, kind, point)
        payload["location_basis"] = pin["method"] if pin else "original_location"
        if pin:
            payload.update(
                uncertainty_m=pin.get("uncertainty_m"),
                uncertainty_basis=pin.get("uncertainty_basis"),
            )
        count = payload["registered_count"] if kind == "commerce" else len(payload["items"])
        complete = payload["complete"]
        return Collected(
            "partial" if not complete else "known" if count else "empty",
            None if complete else "bounded_or_incomplete_catalog",
            payload,
            retrieved,
            **meta,
        )
    except PublicSourceError as exc:
        return Collected("unavailable", exc.reason, **meta)
    except (ValueError, KeyError, TypeError, OSError, OverflowError, ShapelyError):
        return Collected("unavailable", "catalog_not_ready", **meta)
