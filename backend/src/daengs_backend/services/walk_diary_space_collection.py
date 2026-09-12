"""Bounded public spatial acquisition for already selected scenes, outside DB locks.

Park catalogs are prepared separately. Commerce prefers a complete covering catalog;
on a miss it may query the configured public API. Land cover is a WMS point query.
No river, Place, Kakao, behavior inference or source-to-source admission dependency.
"""

import asyncio
import math
from datetime import UTC, datetime

import httpx

from daengs_backend.services import walk_area_catalog, walk_catalog_regions, walk_park_catalog
from daengs_backend.services.walk_commerce_catalog import ENDPOINT as COMMERCE_ENDPOINT
from daengs_backend.services.walk_public_http import get_json
from daengs_backend.services.walk_space_catalog_input import normalization_input, retain_page
from daengs_walk.diary_input import SavedBackground, digest
from daengs_walk.diary_scene_backgrounds import (
    SceneBackgroundSnapshot,
    board_background_revision,
    scene_background_targets,
)
from daengs_walk.diary_space_materials import SpaceInput, normalize_spaces

LAND_ENDPOINT = "https://api.mcee.go.kr/geoserver/wms"
COLLECTION_SECONDS = 4.0
MAX_SCENES = 12


def land_parameters(point, layer):
    x = 6378137 * math.radians(point["lng"])
    y = 6378137 * math.log(math.tan(math.pi / 4 + math.radians(point["lat"]) / 2))
    return {
        "SERVICE": "WMS",
        "VERSION": "1.3.0",
        "REQUEST": "GetFeatureInfo",
        "LAYERS": layer,
        "QUERY_LAYERS": layer,
        "CRS": "EPSG:3857",
        "BBOX": f"{x - 60},{y - 60},{x + 60},{y + 60}",
        "WIDTH": 101,
        "HEIGHT": 101,
        "I": 50,
        "J": 50,
        "INFO_FORMAT": "application/json",
        "FEATURE_COUNT": 10,
    }


def cached_area(kind, point, radius, park_catalog):
    value = (
        walk_park_catalog.read_catalog(park_catalog)
        if kind == "park"
        else walk_catalog_regions.select("commerce", point, radius_m=radius)
    )
    return normalization_input(value, kind, point, radius), datetime.fromisoformat(
        value["retrieved_at"]
    )


async def collect_spaces(
    board,
    *,
    commerce_key="",
    park_catalog="",
    radius_m=1000,
    land_layer="EGIS:lv3_2025y",
    transport=None,
    timeout_s=COLLECTION_SECONDS,
):
    if transport is None:
        async with httpx.AsyncHTTPTransport() as owned:
            return await collect_spaces(
                board,
                commerce_key=commerce_key,
                park_catalog=park_catalog,
                radius_m=radius_m,
                land_layer=land_layer,
                transport=owned,
                timeout_s=timeout_s,
            )
    targets = scene_background_targets(board)
    points = {
        digest(t.anchor.point): t.anchor.point
        for t in targets
        if t.anchor.point is not None and t.anchor.position_state != "provisional"
    }
    results = {}
    semaphore = asyncio.Semaphore(4)

    async def acquire(key, point, kind):
        try:
            async with semaphore:
                at = datetime.now(UTC)
                query = point.model_dump()
                if kind == "land_cover":
                    response = await get_json(
                        transport, LAND_ENDPOINT, land_parameters(query, land_layer)
                    )
                    raw = {"query_point": query, "layer": land_layer, "response": response}
                else:
                    try:
                        raw, at = await asyncio.to_thread(
                            cached_area, kind, query, radius_m, park_catalog
                        )
                    except (OSError, ValueError, TypeError, KeyError):
                        if kind != "commerce" or not commerce_key:
                            raise ValueError("catalog unavailable") from None
                        pages = []
                        await walk_area_catalog.pages(
                            transport,
                            COMMERCE_ENDPOINT,
                            commerce_key,
                            {"cx": query["lng"], "cy": query["lat"], "radius": radius_m},
                            page_sink=lambda page: pages.append(retain_page(page, "commerce")),
                        )
                        raw = {"query_point": query, "radius_m": radius_m, "pages": pages}
                value = await asyncio.to_thread(
                    normalize_spaces, SpaceInput(point=point, **{kind: raw})
                )
                value = value.model_copy(
                    update={"audit": tuple(a for a in value.audit if a["source"] == kind)}
                )
                results[(key, kind)] = (value, at)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - URLs/credentials/paths never enter saved diagnostics
            results[(key, kind)] = (None, None)

    tasks = []
    if len(board.scenes) <= MAX_SCENES:
        tasks = [
            asyncio.create_task(acquire(key, point, kind))
            for key, point in points.items()
            for kind in ("commerce", "park", "land_cover")
        ]
    try:
        if tasks:
            await asyncio.wait(tasks, timeout=timeout_s)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    backgrounds = []
    for target in targets:
        for kind in ("commerce", "park", "land_cover"):
            key = (digest(target.anchor.point), kind)
            value, at = results.get(key, (None, None))
            unlocated = target.anchor.point is None or target.anchor.position_state == "provisional"
            reason = (
                "scene_location_unavailable"
                if unlocated
                else "scene_limit"
                if len(board.scenes) > MAX_SCENES
                else "collection_timeout"
                if key not in results
                else "source_unavailable"
            )
            payload = value.model_dump(mode="json") if value is not None else None
            backgrounds.append(
                SavedBackground(
                    id="normalized:"
                    + digest(
                        [target.scene_id, target.core_ref.model_dump(mode="json"), kind, payload]
                    ),
                    target=target.core_ref,
                    provider="public-normalized-" + kind,
                    payload_schema="space-materials-v1",
                    policy_version="space-normalization-v1",
                    query_point=target.anchor.point,
                    tags=("space",),
                    status=(
                        "known"
                        if value is not None
                        else "not_requested"
                        if unlocated
                        else "unavailable"
                    ),
                    reason=None if value is not None else reason,
                    retrieved_at=at,
                    temporal_basis="lookup_snapshot",
                    payload=payload,
                    payload_sha256=digest(payload) if payload is not None else None,
                )
            )
    return SceneBackgroundSnapshot(
        board_revision=board_background_revision(board),
        targets=targets,
        backgrounds=tuple(backgrounds),
    )


async def configured_collection(board):
    from daengs_backend.config import settings

    return await collect_spaces(
        board,
        commerce_key=settings.walk_public_data_key.get_secret_value().strip(),
        park_catalog=settings.walk_park_catalog_path,
        radius_m=settings.walk_diary_space_radius_m,
        land_layer=settings.walk_land_cover_layer,
    )
