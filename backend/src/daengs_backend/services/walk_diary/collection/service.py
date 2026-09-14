"""Bounded public spatial acquisition for already selected scenes, outside DB locks.

Park catalogs are prepared separately. Commerce prefers a complete covering catalog;
on a miss it may query the configured public API. Land cover is a WMS point query.
No river, Place, Kakao, behavior inference or source-to-source admission dependency.
"""

import asyncio
import math
from datetime import UTC, datetime

import httpx

from daengs_backend.orchestration.execution import discard
from daengs_backend.services import walk_area_catalog, walk_catalog_regions, walk_park_catalog
from daengs_backend.services.walk_commerce_catalog import ENDPOINT as COMMERCE_ENDPOINT
from daengs_backend.services.walk_diary.collection.progress import collection_progress
from daengs_backend.services.walk_diary.collection.snapshot import source_background
from daengs_backend.services.walk_public_http import get_json
from daengs_backend.services.walk_sgis import sgis
from daengs_backend.services.walk_space_catalog_input import normalization_input, retain_page
from daengs_walk.diary.board.backgrounds import scene_background_targets
from daengs_walk.diary.contracts.input import digest
from daengs_walk.diary.space.materials import SpaceInput, normalize_spaces

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
    sgis_key="",
    sgis_secret="",
    include_sgis=False,
    include_spaces=True,
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
                sgis_key=sgis_key,
                sgis_secret=sgis_secret,
                include_sgis=include_sgis,
                include_spaces=include_spaces,
            )
    targets = scene_background_targets(board)
    points = {
        digest(t.anchor.point): t.anchor.point
        for t in targets
        if t.anchor.point is not None and t.anchor.position_state != "provisional"
    }
    kinds = (*(("sgis",) if include_sgis else ()), "commerce", "park", "land_cover")
    # One lane per source, at most four active calls. A slow WMS cannot occupy address slots.
    lanes = {kind: asyncio.Semaphore(1) for kind in kinds}
    progress = collection_progress(board, timeout_s)
    selected_points = dict(list(points.items())[:MAX_SCENES])
    scenes = {scene.id: scene for scene in board.scenes}
    grouped = {}
    for target in targets:
        grouped.setdefault(digest(target.anchor.point), []).append(target)

    def backgrounds(key, kind, value=None, at=None, reason="source_unavailable"):
        return tuple(
            source_background(t, scenes[t.scene_id], kind, value, at, reason) for t in grouped[key]
        )

    for key in grouped:
        for kind in kinds:
            pending = backgrounds(
                key,
                kind,
                reason="scene_limit" if key not in selected_points else "collection_not_started",
            )
            progress.expect((key, kind), pending)
            if key not in selected_points:
                progress.finish((key, kind), pending)

    async def acquire(key, point, kind):
        try:
            async with lanes[kind]:
                if not progress.start((key, kind)):
                    return
                at = datetime.now(UTC)
                query = point.model_dump()
                if kind == "sgis":
                    if not sgis_key or not sgis_secret:
                        raise ValueError("sgis unavailable")
                    row, retrieved = await sgis.address(transport, sgis_key, sgis_secret, query)
                    progress.finish(
                        (key, kind), backgrounds(key, kind, row, datetime.fromisoformat(retrieved))
                    )
                    return
                if not include_spaces:
                    raise ValueError("spatial source disabled")
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
                progress.finish((key, kind), backgrounds(key, kind, value, at))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - URLs/credentials/paths never enter saved diagnostics
            progress.finish((key, kind), backgrounds(key, kind))

    tasks = [
        asyncio.create_task(acquire(key, point, kind))
        for key, point in selected_points.items()
        for kind in kinds
    ]
    try:
        if tasks:
            remaining = max(0, progress.deadline - asyncio.get_running_loop().time())
            await asyncio.wait(tasks, timeout=min(timeout_s, remaining))
        # Commit the snapshot before cancellation/transport cleanup. Late providers have no write access.
        return progress.freeze()
    finally:
        for task in tasks:
            discard(task)


async def configured_collection(board):
    from daengs_backend.config import settings

    return await collect_spaces(
        board,
        commerce_key=settings.walk_public_data_key.get_secret_value().strip(),
        park_catalog=settings.walk_park_catalog_path,
        radius_m=settings.walk_diary_space_radius_m,
        land_layer=settings.walk_land_cover_layer,
        sgis_key=settings.walk_sgis_key.get_secret_value().strip(),
        sgis_secret=settings.walk_sgis_secret.get_secret_value().strip(),
        include_sgis=True,
        include_spaces=settings.walk_diary_space_enabled,
    )
