"""Public background providers behind the existing revision-fenced entry collector."""

import asyncio

import httpx

from daengs_backend.config import settings
from daengs_backend.services.walk_entry_context_source import Collected
from daengs_backend.services.walk_park_catalog import nearby_parks, read_catalog
from daengs_backend.services.walk_public_http import PublicSourceError
from daengs_backend.services.walk_sgis import sgis


async def collect_public(tag, point, pin=None, *, transport=None):
    if not settings.walk_public_context_enabled:
        return Collected("not_requested", "provider_not_connected")
    # Legacy locations also carry captured_at/accuracy; the query identity is coordinates only.
    point = {"lat": point["lat"], "lng": point["lng"]}
    address = tag == "space.address"
    provider = "sgis" if address else "data-go-kr-parks"
    operation = "rgeocode:20" if address else "national-park-point-catalog"
    meta = {"provider": provider, "operation": operation}
    if (not address and not settings.walk_park_catalog_path) or (
        address
        and (
            not settings.walk_sgis_key.get_secret_value()
            or not settings.walk_sgis_secret.get_secret_value()
        )
    ):
        return Collected("not_requested", "provider_not_configured", **meta)
    try:
        if address:
            if transport is None:
                async with httpx.AsyncHTTPTransport() as opened:
                    return await collect_public(tag, point, pin, transport=opened)
            item, retrieved = await asyncio.wait_for(
                sgis.address(
                    transport,
                    settings.walk_sgis_key.get_secret_value(),
                    settings.walk_sgis_secret.get_secret_value(),
                    point,
                ),
                timeout=25,
            )
            payload = {
                "format": "sgis-dong-v1",
                "address": item,
                "address_type": "administrative_dong",
                "query_point": point,
            }
            status, reason = ("known", None) if item else ("empty", None)
        else:
            catalog = await asyncio.to_thread(read_catalog, settings.walk_park_catalog_path)
            parks, partial = await asyncio.to_thread(nearby_parks, catalog, point)
            retrieved = catalog["retrieved_at"]
            payload = {
                "format": "public-park-nearby-v1",
                "items": parks,
                "radius_m": 250,
                "geometry": "registered_point",
                "query_point": point,
                "visit_confirmed": False,
                "catalog_sha256": catalog["parks_sha256"],
                "coverage": "national_catalog_registered_points",
                "rejected_catalog_rows": catalog["rejected_rows"],
            }
            status = "partial" if partial else "known" if parks else "empty"
            reason = "bounded_or_incomplete_catalog" if partial else None
        payload.update(location_basis=pin["method"] if pin else "original_location")
        if pin:
            payload.update(
                uncertainty_m=pin.get("uncertainty_m"),
                uncertainty_basis=pin.get("uncertainty_basis"),
            )
        return Collected(status, reason, payload, retrieved, **meta)
    except PublicSourceError as exc:
        return Collected("unavailable", exc.reason, retryable=exc.retryable, **meta)
    except TimeoutError:
        return Collected("unavailable", "provider_timeout", retryable=True, **meta)
    except (ValueError, KeyError, TypeError, OSError, OverflowError):
        return Collected(
            "unavailable", "invalid_address_response" if address else "catalog_not_ready", **meta
        )
