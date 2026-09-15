"""Real source acquisition into relational preparation, outside writing and DB locks."""

import asyncio
from dataclasses import replace

import httpx

from daengs_backend.services.walk_background.providers.sgis import sgis
from daengs_backend.services.walk_diary.collection.service import MAX_SCENES, collect_spaces
from daengs_backend.services.walk_diary.preparation.relational import prepare_relational_diary
from daengs_walk.value_contracts import digest


async def collect_road_snapshots(board, *, transport, key, secret, timeout_s=4.0, scene_ids=None):
    """Bounded exact-point queries. Missing/failed/empty are retained, never road disappearance."""
    selected = set(scene_ids) if scene_ids is not None else {s.id for s in board.scenes}
    if not selected <= {s.id for s in board.scenes}:
        raise ValueError("unknown relation scene")
    points = {
        digest(s.anchor.point): s.anchor.point.model_dump(mode="json")
        for s in board.scenes
        if s.id in selected
        and s.anchor.point is not None
        and s.anchor.position_state != "provisional"
    }
    snapshots = []
    deadline = asyncio.get_running_loop().time() + timeout_s
    for index, point in enumerate(points.values()):
        saved = {
            "point": point,
            "addr_type": 10,
            "provider": "sgis",
            "schema_version": "sgis-road-snapshot-v1",
            "status": "unavailable",
        }
        snapshots.append(saved)
        if not key or not secret:
            saved.update(status="not_requested", reason="credentials_unavailable")
            continue
        if index >= MAX_SCENES:
            saved.update(status="not_requested", reason="scene_limit")
            continue
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            saved["reason"] = "collection_deadline"
            continue
        try:
            async with asyncio.timeout(remaining):
                row, at = await sgis.address(transport, key, secret, point, addr_type=10)
            saved.update(
                status="known" if row else "empty",
                retrieved_at=at,
                response={"errCd": 0, "result": [row] if row else []},
            )
        except TimeoutError:
            saved["reason"] = "collection_deadline"
        except Exception:  # noqa: BLE001 -- provider exceptions can contain request secrets
            saved["reason"] = "source_unavailable"
    return tuple(snapshots)


async def collect_and_prepare_relational(
    base,
    *,
    transport=None,
    scene_ids=None,
    timeout_s=4.0,
    sgis_key="",
    sgis_secret="",
    **space_options,
):
    """Use real scene-source snapshots; no old with_scene_backgrounds/slot reassembly."""
    if transport is None:
        async with httpx.AsyncHTTPTransport() as owned:
            return await collect_and_prepare_relational(
                base,
                transport=owned,
                scene_ids=scene_ids,
                timeout_s=timeout_s,
                sgis_key=sgis_key,
                sgis_secret=sgis_secret,
                **space_options,
            )
    selected = tuple(scene_ids) if scene_ids is not None else None
    if selected is not None and not set(selected) <= {s.id for s in base.board.scenes}:
        raise ValueError("unknown relation scene")
    backgrounds = await collect_spaces(
        base.board,
        transport=transport,
        timeout_s=timeout_s,
        sgis_key=sgis_key,
        sgis_secret=sgis_secret,
        include_sgis=True,
        **space_options,
    )
    roads = await collect_road_snapshots(
        base.board,
        transport=transport,
        key=sgis_key,
        secret=sgis_secret,
        timeout_s=timeout_s,
        scene_ids=selected,
    )
    return prepare_relational_diary(
        replace(base, scene_backgrounds=backgrounds.validate_board(base.board)),
        scene_ids=selected,
        road_snapshots=roads,
    )


async def configured_relational_preparation(base, *, scene_ids=None):
    from daengs_backend.config import settings

    return await collect_and_prepare_relational(
        base,
        scene_ids=scene_ids,
        sgis_key=settings.walk_sgis_key.get_secret_value().strip(),
        sgis_secret=settings.walk_sgis_secret.get_secret_value().strip(),
        commerce_key=settings.walk_public_data_key.get_secret_value().strip(),
        park_catalog=settings.walk_park_catalog_path,
        radius_m=settings.walk_diary_space_radius_m,
        land_layer=settings.walk_land_cover_layer,
        include_spaces=settings.walk_diary_space_enabled,
    )
