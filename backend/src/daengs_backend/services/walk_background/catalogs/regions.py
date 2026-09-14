"""Stable 1 km regions for public data; never merge overlapping catalog counts."""

import math
from pathlib import Path

from daengs_backend.config import settings
from daengs_backend.services.walk_background.catalogs import area as catalog
from daengs_backend.services.walk_background.http import PublicSourceError

RADIUS_M = 1200
REFRESH_DAYS = 20


def region(point):
    catalog.area(point, RADIUS_M)  # finite Korean coordinates, including type validation
    x, y = catalog.xy(point)
    ix, iy = math.floor(x / 1000), math.floor(y / 1000)
    lng, lat = catalog.REVERSE.transform(ix * 1000 + 500, iy * 1000 + 500)
    center = {"lat": round(lat, 8), "lng": round(lng, 8)}
    catalog.area(center, RADIUS_M)
    return f"kr1k-{ix}-{iy}", center


def path_for(kind, point):
    if kind not in {"commerce", "river"} or not settings.walk_public_catalog_root:
        raise ValueError("regional catalogs not configured")
    key, _ = region(point)
    return Path(settings.walk_public_catalog_root) / key / f"{kind}.json"


def automatic_ready():
    return bool(
        settings.walk_catalog_refresh_enabled
        and settings.walk_entry_context_enabled
        and settings.walk_public_context_enabled
        and settings.walk_area_context_enabled
        and settings.walk_entry_v2_enabled
        and settings.redis_url
        and settings.walk_public_catalog_root
        and settings.walk_park_catalog_path
        and settings.walk_public_data_key.get_secret_value().strip()
    )


def can_prepare(point):
    if not automatic_ready():
        return False
    try:
        region(point)
    except (ValueError, TypeError, KeyError, OverflowError):
        return False
    return True


def select(kind, point, *, radius_m=None):
    """Prefer the managed region; a still-valid legacy snapshot is a rollout fallback."""
    paths = []
    if settings.walk_public_catalog_root:
        paths.append(path_for(kind, point))
    legacy = getattr(settings, f"walk_{kind}_catalog_path")
    if legacy:
        paths.append(Path(legacy))
    radius = radius_m if radius_m is not None else (125 if kind == "commerce" else 250)
    outside = False
    for path in paths:
        try:
            value = catalog.read(path, kind)
            if catalog.covers(value, point, radius):
                return value
            outside = True
        except (ValueError, TypeError, KeyError, OSError, OverflowError):
            continue
    if outside:
        raise PublicSourceError("outside_catalog_coverage")
    raise ValueError("no valid catalog covers the whole query")
