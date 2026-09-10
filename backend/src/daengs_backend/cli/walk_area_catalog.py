"""Explicit regional refresh, outside the entry worker lease and public request path."""

import argparse
import asyncio

import httpx

from daengs_backend.config import settings
from daengs_backend.services import walk_commerce_catalog, walk_river_catalog
from daengs_backend.services.walk_public_http import PublicSourceError


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("commerce", "river"))
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lng", type=float, required=True)
    parser.add_argument("--radius", type=int, default=1200)
    args = parser.parse_args()
    key = settings.walk_public_data_key.get_secret_value().strip()
    path = getattr(settings, f"walk_{args.kind}_catalog_path")
    if not path:
        raise SystemExit(f"Set DAENGS_WALK_{args.kind.upper()}_CATALOG_PATH")
    if args.kind == "commerce" and not key:
        raise SystemExit("Set DAENGS_WALK_PUBLIC_DATA_KEY")
    source = walk_commerce_catalog if args.kind == "commerce" else walk_river_catalog
    try:
        async with httpx.AsyncHTTPTransport() as transport:
            result = await source.refresh(
                transport, key, path, {"lat": args.lat, "lng": args.lng}, args.radius
            )
    except PublicSourceError as exc:
        raise SystemExit("Area catalog refresh failed: " + exc.reason) from None
    except (ValueError, KeyError, TypeError, OSError, OverflowError) as exc:
        raise SystemExit("Area catalog refresh failed: " + type(exc).__name__) from None
    print(
        f"kind={args.kind} rows={len(result['rows'])} rejected={result['rejected_rows']} pages={len(result['pages'])}"
    )
    if args.kind == "river":
        print(
            f"standard_status={result['standard']['status']} reason={result['standard']['reason']}"
        )


if __name__ == "__main__":
    asyncio.run(main())
