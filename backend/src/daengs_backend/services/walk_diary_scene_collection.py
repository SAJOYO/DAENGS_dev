"""Explicit comparison collection for fixed scene cores. No DB or publication writes."""

import asyncio
from datetime import UTC, datetime

import httpx

from daengs_walk.diary_input import SavedBackground, digest
from daengs_walk.diary_scene_backgrounds import (
    SceneBackgroundSnapshot,
    board_background_revision,
    scene_background_targets,
)


async def collect_scene_backgrounds(board, *, place_api, kakao_key="", transport=None):
    targets = scene_background_targets(board)
    semaphore = asyncio.Semaphore(4)

    def saved(target, provider, schema, status, payload=None, reason=None):
        captured = datetime.now(UTC)
        return SavedBackground(
            id="scene-query:"
            + digest([target.model_dump(mode="json"), provider, captured.isoformat(), payload]),
            target=target.core_ref,
            provider=provider,
            payload_schema=schema,
            policy_version=schema,
            query_point=target.anchor.point,
            tags=("space",),
            status=status,
            reason=reason,
            retrieved_at=captured if status != "not_requested" else None,
            temporal_basis="lookup_snapshot",
            payload=payload,
            payload_sha256=digest(payload) if payload is not None else None,
        )

    async with httpx.AsyncClient(timeout=8, transport=transport) as client:

        async def collect(target):
            if len(targets) > 12:
                return [
                    saved(
                        target,
                        "place-search",
                        "walk-entry-context-v1",
                        "not_requested",
                        reason="collection_scene_budget",
                    )
                ]
            if target.anchor.point is None or target.anchor.position_state == "provisional":
                return [
                    saved(
                        target,
                        "place-search",
                        "walk-entry-context-v1",
                        "not_requested",
                        reason="scene_location_unavailable",
                    )
                ]
            async with semaphore:
                point = target.anchor.point.model_dump(mode="json")
                results = []
                place_count = 0
                try:
                    response = await client.post(
                        place_api.rstrip("/") + "/v2/places/search",
                        json={
                            **point,
                            "radius_m": 250,
                            "kinds": ["leisure", "cafe", "restaurant"],
                            "limit_per_kind": 10,
                        },
                    )
                    response.raise_for_status()
                    raw = response.json()
                    items = [
                        {"kind": group["kind"], "place": hit["place"]}
                        for group in raw["groups"]
                        for hit in group["results"]
                    ]
                    place_count = len(items)
                    payload = {
                        "geometry": "registered_location",
                        "visit_confirmed": False,
                        "radius_m": 250,
                        "coverage": "selected_place_categories_only",
                        "items": items[:30],
                        "raw_response": raw,
                    }
                    results.append(
                        saved(
                            target,
                            "place-search",
                            "walk-entry-context-v1",
                            "known" if items else "empty",
                            payload,
                        )
                    )
                except (httpx.HTTPError, ValueError, KeyError, TypeError):
                    results.append(
                        saved(
                            target,
                            "place-search",
                            "walk-entry-context-v1",
                            "unavailable",
                            reason="place_lookup_failed",
                        )
                    )
                if not kakao_key:
                    results.append(
                        saved(
                            target,
                            "kakao-local",
                            "scene-kakao-context-v1",
                            "not_requested",
                            reason="provider_not_configured",
                        )
                    )
                    return results
                payload = {
                    "query_point": point,
                    "radius_m": 250,
                    "search_responses": [],
                    "address_response": None,
                }
                failed = False
                headers = {"Authorization": "KakaoAK " + kakao_key}
                if not place_count:
                    for category, operation, query in (
                        ("park", "keyword", {"query": "공원"}),
                        ("CE7", "category", {"category_group_code": "CE7"}),
                        ("FD6", "category", {"category_group_code": "FD6"}),
                    ):
                        try:
                            response = await client.get(
                                f"https://dapi.kakao.com/v2/local/search/{operation}.json",
                                params={
                                    **query,
                                    "x": point["lng"],
                                    "y": point["lat"],
                                    "radius": 250,
                                    "sort": "distance",
                                    "size": 15,
                                },
                                headers=headers,
                            )
                            response.raise_for_status()
                            payload["search_responses"].append(
                                {"category": category, "body": response.json()}
                            )
                        except (httpx.HTTPError, ValueError):
                            failed = True
                try:
                    response = await client.get(
                        "https://dapi.kakao.com/v2/local/geo/coord2address.json",
                        params={"x": point["lng"], "y": point["lat"]},
                        headers=headers,
                    )
                    response.raise_for_status()
                    payload["address_response"] = response.json()
                except (httpx.HTTPError, ValueError):
                    failed = True
                if (
                    failed
                    and not payload["search_responses"]
                    and payload["address_response"] is None
                ):
                    results.append(
                        saved(
                            target,
                            "kakao-local",
                            "scene-kakao-context-v1",
                            "unavailable",
                            reason="kakao_lookup_failed",
                        )
                    )
                else:
                    results.append(
                        saved(
                            target,
                            "kakao-local",
                            "scene-kakao-context-v1",
                            "partial" if failed else "known",
                            payload,
                            "some_queries_unavailable" if failed else None,
                        )
                    )
                return results

        async def bounded(target):
            try:
                return await asyncio.wait_for(collect(target), timeout=20)
            except TimeoutError:
                return [
                    saved(
                        target,
                        "place-search",
                        "walk-entry-context-v1",
                        "unavailable",
                        reason="collection_timeout",
                    )
                ]

        batches = await asyncio.gather(*(bounded(target) for target in targets))
    return SceneBackgroundSnapshot(
        board_revision=board_background_revision(board),
        targets=targets,
        backgrounds=tuple(item for batch in batches for item in batch),
    ).validate_board(board)
