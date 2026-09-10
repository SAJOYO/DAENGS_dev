"""Read-only runtime inventory. Never print credentials or user record contents."""

import asyncio
import json
from pathlib import Path

FLAGS = (
    "walk_entry_context_enabled",
    "walk_public_context_enabled",
    "walk_area_context_enabled",
    "walk_entry_v2_enabled",
    "walk_entry_v2_write_enabled",
    "walk_photo_metadata_enabled",
    "walk_diary_enabled",
)
KEYS = ("walk_sgis_key", "walk_sgis_secret", "walk_public_data_key", "gemini_api_key")
TABLES = (
    "walk_entries",
    "walk_entry_context_jobs",
    "walk_entry_context_envelopes",
    "walk_entry_pins",
    "walk_entry_mutations",
    "walk_photo_manifests",
    "walk_storyboards",
)


async def inventory():
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from daengs_backend.config import settings

    result = {
        "format": "walk-runtime-inventory-v1",
        "flags": {key: getattr(settings, key, None) for key in FLAGS},
        "keys_present": {
            key: bool(
                getattr(settings, key, None) and getattr(settings, key).get_secret_value().strip()
            )
            for key in KEYS
        },
        "catalogs": {},
    }
    for kind in ("park", "commerce", "river"):
        path = getattr(settings, f"walk_{kind}_catalog_path", "")
        result["catalogs"][kind] = {
            "configured": bool(path),
            "file_exists": bool(path and Path(path).is_file()),
        }
    engine = create_async_engine(
        settings.database_url,
        echo=False,
        poolclass=NullPool,
        connect_args={"timeout": 5},
    )
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            await connection.execute(text("SET LOCAL statement_timeout = '5000'"))
            result["tables"] = {
                table: bool(
                    await connection.scalar(text("SELECT to_regclass(:name)"), {"name": table})
                )
                for table in TABLES
            }
            if result["tables"]["walk_entry_context_jobs"]:
                result["context_tag_constraint"] = await connection.scalar(
                    text(
                        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conrelid = 'walk_entry_context_jobs'::regclass "
                        "AND conname = 'walk_entry_context_jobs_tag_check'"
                    )
                )
                result["context_jobs"] = [
                    dict(row)
                    for row in (
                        await connection.execute(
                            text(
                                "SELECT tag, state, count(*) AS count FROM walk_entry_context_jobs "
                                "GROUP BY tag, state ORDER BY tag, state"
                            )
                        )
                    ).mappings()
                ]
    except Exception as exc:  # noqa: BLE001 - never expose connection values in diagnostic logs
        result["database_error"] = type(exc).__name__
    finally:
        await engine.dispose()
    return result


if __name__ == "__main__":
    try:
        print(json.dumps(asyncio.run(inventory()), ensure_ascii=True))
    except Exception as exc:  # noqa: BLE001 - report only the failure class, not settings/secrets
        print(json.dumps({"inventory_error": type(exc).__name__}))
        raise SystemExit(1) from None
