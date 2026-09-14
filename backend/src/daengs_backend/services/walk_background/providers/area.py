"""Regional cache -> the same fenced context envelope, with no entry-worker API calls."""

import asyncio

from shapely.errors import ShapelyError

from daengs_backend.config import settings
from daengs_backend.services.walk_background.catalogs import commerce as walk_commerce_catalog
from daengs_backend.services.walk_background.catalogs import regions as walk_catalog_regions
from daengs_backend.services.walk_background.catalogs import river as walk_river_catalog
from daengs_backend.services.walk_background.contracts import Collected
from daengs_backend.services.walk_background.http import PublicSourceError


def snapshot(kind, point):
    value = walk_catalog_regions.select(kind, point)
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
    if not getattr(settings, f"walk_{kind}_catalog_path") and not settings.walk_public_catalog_root:
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
        if walk_catalog_regions.can_prepare(point):
            return Collected("unavailable", "catalog_preparing", retryable=True, **meta)
        return Collected("unavailable", exc.reason, **meta)
    except (ValueError, KeyError, TypeError, OSError, OverflowError, ShapelyError):
        if walk_catalog_regions.can_prepare(point):
            return Collected("unavailable", "catalog_preparing", retryable=True, **meta)
        return Collected("unavailable", "catalog_not_ready", **meta)
