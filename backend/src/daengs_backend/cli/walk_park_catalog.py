"""python -m daengs_backend.cli.walk_park_catalog; no user records or model calls."""

import asyncio

import httpx

from daengs_backend.config import settings
from daengs_backend.services.walk_park_catalog import refresh_catalog
from daengs_backend.services.walk_public_http import PublicSourceError


async def main():
    key = settings.walk_public_data_key.get_secret_value()
    path = settings.walk_park_catalog_path
    if not key or not path:
        raise SystemExit("Set DAENGS_WALK_PUBLIC_DATA_KEY and DAENGS_WALK_PARK_CATALOG_PATH")
    try:
        async with httpx.AsyncHTTPTransport() as transport:
            result = await refresh_catalog(transport, key, path)
    except PublicSourceError as exc:
        raise SystemExit("Park catalog refresh failed: " + exc.reason) from None
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise SystemExit("Park catalog refresh failed: " + type(exc).__name__) from None
    print(
        f"parks={len(result['parks'])} rejected={result['rejected_rows']} pages={len(result['pages'])}"
    )


if __name__ == "__main__":
    asyncio.run(main())
