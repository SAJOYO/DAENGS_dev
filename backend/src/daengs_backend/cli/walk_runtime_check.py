"""Read-only rollout preflight; JSON contains status/metadata, never keys or user records."""

import argparse
import asyncio
import json
import os
from pathlib import Path

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from daengs_backend.config import settings
from daengs_backend.services import (
    walk_area_catalog,
    walk_catalog_regions,
    walk_commerce_catalog,
    walk_park_catalog,
    walk_river_catalog,
)
from daengs_backend.services.walk_sgis import sgis

FLAGS = (
    "walk_entry_context_enabled",
    "walk_public_context_enabled",
    "walk_area_context_enabled",
    "walk_diary_enabled",
)
KEYS = ("walk_sgis_key", "walk_sgis_secret", "walk_public_data_key", "gemini_api_key")
VERIFIERS = (
    "2026-09-05_walk_entries",
    "2026-09-05_walk_storyboards",
    "2026-09-08_walk_entry_contexts",
    "2026-09-09_walk_public_context_commerce",
    "2026-09-10_walk_context_recollection",
)


def catalogs(point):
    """Use the production readers: checksum, freshness, geometry and query coverage."""
    result = {}
    park = walk_park_catalog.read_catalog(settings.walk_park_catalog_path)
    nearby, partial = walk_park_catalog.nearby_parks(park, point)
    result["park"] = {
        "retrieved_at": park["retrieved_at"],
        "nearby_count": len(nearby),
        "partial": partial,
        "sha256": park["parks_sha256"],
    }
    for kind, source in (("commerce", walk_commerce_catalog), ("river", walk_river_catalog)):
        value = walk_catalog_regions.select(kind, point)
        nearby = source.nearby(value, point)
        result[kind] = {
            "retrieved_at": value["retrieved_at"],
            "area": value["area"],
            "partial": not nearby["complete"],
            "nearby_count": nearby["registered_count"]
            if kind == "commerce"
            else len(nearby["items"]),
        }
    return result


async def verify_schema(directory, database_url):
    names = list(VERIFIERS)
    if settings.walk_entry_v2_enabled or settings.walk_entry_v2_write_enabled:
        names.append("2026-09-09_walk_entry_pins")
    if settings.walk_photo_metadata_enabled:
        names.append("2026-09-09_walk_photo_manifests")
    scripts = [(directory / f"verify_{name}.sql").read_text(encoding="utf-8") for name in names]
    engine = create_async_engine(
        database_url, echo=False, poolclass=NullPool, connect_args={"timeout": 5}
    )
    try:
        async with engine.connect() as connection:
            raw = await connection.get_raw_connection()
            # Existing DO verifiers use only schema metadata. No schema/data writes allowed.
            async with raw.driver_connection.transaction(readonly=True):
                await raw.driver_connection.execute("SET LOCAL statement_timeout = '5000'")
                for script in scripts:
                    await raw.driver_connection.execute(script)
    finally:
        await engine.dispose()
    return names


async def check(args):
    result = {"format": "walk-runtime-check-v1", "ready": False, "checks": {}, "errors": {}}

    async def attempt(name, function):
        try:
            result["checks"][name] = await function()
        except Exception as exc:  # noqa: BLE001 - connection and validation errors can contain secrets
            result["errors"][name] = type(exc).__name__

    flags = {key: getattr(settings, key) for key in FLAGS}
    result["checks"]["flags"] = flags
    result["checks"]["catalog_refresh_enabled"] = settings.walk_catalog_refresh_enabled
    if settings.walk_catalog_refresh_enabled and not settings.walk_public_catalog_root:
        result["errors"]["catalog_refresh"] = "regional_root_missing"
    if not args.allow_disabled and not all(flags.values()):
        result["errors"]["flags"] = "required_flags_disabled"
    present = {key: bool(getattr(settings, key).get_secret_value().strip()) for key in KEYS}
    result["checks"]["keys_present"] = present
    if not args.startup and not all(present.values()):
        result["errors"]["keys"] = "required_keys_missing"
    if not args.startup:
        await attempt("catalogs", lambda: asyncio.to_thread(catalogs, args.point))
    if args.probe_address:

        async def probe_address():
            async with httpx.AsyncHTTPTransport() as transport:
                address, _ = await asyncio.wait_for(
                    sgis.address(
                        transport,
                        settings.walk_sgis_key.get_secret_value(),
                        settings.walk_sgis_secret.get_secret_value(),
                        args.point,
                    ),
                    timeout=25,
                )
            if not address:
                raise ValueError("no administrative dong at probe point")
            return {"status": "known"}

        await attempt("sgis", probe_address)
    await attempt("schema", lambda: verify_schema(args.schema_dir, settings.database_url))

    async def redis_ping():
        url = os.environ.get("REDIS_URL")
        if not url:
            raise ValueError("missing broker configuration")
        async with Redis.from_url(url, socket_connect_timeout=5, socket_timeout=5) as broker:
            return bool(await broker.ping())

    await attempt("broker", redis_ping)
    result["ready"] = not result["errors"]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lat", type=float)
    parser.add_argument("--lng", type=float)
    parser.add_argument("--schema-dir", type=Path, default=Path("/schema-checks"))
    parser.add_argument("--allow-disabled", action="store_true")
    parser.add_argument("--startup", action="store_true")
    parser.add_argument("--probe-address", action="store_true")
    args = parser.parse_args()
    if args.startup and args.probe_address:
        parser.error("startup does not call public APIs")
    if not args.startup and (args.lat is None or args.lng is None):
        parser.error("catalog preflight requires --lat and --lng")
    args.point = {"lat": args.lat, "lng": args.lng}
    if not args.startup:
        walk_area_catalog.area(args.point, 500)
    result = asyncio.run(check(args))
    print(json.dumps(result, ensure_ascii=True))
    raise SystemExit(0 if result["ready"] else 1)


if __name__ == "__main__":
    main()
