"""Synthetic three-part fixture, using the same canonical motion kernel as stored walks."""

import uuid
from datetime import UTC, datetime, timedelta

from daengs_walk import analyze_walk
from daengs_walk.contracts import WalkEvidencePoint
from daengs_walk.diary_board import VerifiedBoardRoute
from daengs_walk.diary_input import (
    DiaryInput,
    RouteVersion,
    SavedBackground,
    UserRecord,
    digest,
    material_ref,
)
from daengs_walk.diary_observations import build_observation_pool


def demo_input():
    at = datetime(2026, 9, 11, 8, tzinfo=UTC)
    seconds, north, samples = 0, 0, [(0, 0)]
    for count, speed in [(6, 2), (3, 0), (6, 2), (3, 0.7), (6, 2), (3, 4), (6, 2)]:
        for _ in range(count):
            seconds += 10
            north += speed * 10
            samples.append((seconds, north))
    points = tuple(
        WalkEvidencePoint(
            client_seq=i,
            at=at + timedelta(seconds=sec),
            lat=37.5 + metres / 111195,
            lng=127,
            accuracy_m=5,
            is_mock=True,
        )
        for i, (sec, metres) in enumerate(samples)
    )
    walk_id = uuid.UUID(int=454)
    evidence = analyze_walk(walk_id, at, points[-1].at, points)
    route = RouteVersion(
        status="ready",
        analysis_id="synthetic-slots-demo",
        input_fingerprint=digest([p.model_dump(mode="json") for p in points]),
        calculation_version=evidence.facts.calculation_version,
    )
    notes = (
        "  벤치 옆에서 물을 마셨다.\n",
        "골목 모퉁이에서 냄새를 맡았다.",
        "사진을 찍고 다시 걸었다.",
    )
    records, backgrounds = [], []
    for i, (seq, note) in enumerate(zip((8, 18, 28), notes, strict=True), 1):
        fix = points[seq]
        record = UserRecord.model_validate(
            {
                "ref": {
                    "store": "walk_entry",
                    "id": f"demo-{i}",
                    "version": "1",
                    "version_kind": "revision",
                },
                "content": {"kind": "note", "text": note},
                "anchor": {
                    "event_at": fix.at,
                    "time_basis": "recorded_at",
                    "location_at": fix.at,
                    "point": {"lat": fix.lat, "lng": fix.lng},
                    "accuracy_m": 5,
                    "position_state": "resolved",
                    "method": "observed",
                    "source_fixes": [{"client_seq": seq, "chain_index": 0, "at": fix.at}],
                },
            }
        )
        records.append(record)
        payload = {
            "geometry": "registered_location",
            "visit_confirmed": False,
            "radius_m": 250,
            "coverage": "selected_place_categories_only",
            "items": [
                {
                    "kind": kind,
                    "place": {
                        "name": name,
                        "distance_m": metres,
                        "key": {"source": "synthetic", "ref": key},
                    },
                }
                for kind, name, metres, key in (
                    ("leisure", "동네공원", 35 + i * 5, "park"),
                    ("cafe", "골목카페", 100 + i * 10, "cafe"),
                    ("restaurant", "작은식당", 190, "restaurant"),
                    ("cafe", "골목카페", 100 + i * 10, "cafe"),
                )
            ],
        }
        backgrounds.append(
            SavedBackground(
                id=f"space-{i}",
                target=material_ref(record),
                provider="place-search",
                payload_schema="walk-entry-context-v1",
                policy_version="walk-entry-context-v1",
                query_point=record.anchor.point,
                tags=("space",),
                status="known",
                retrieved_at=points[-1].at,
                temporal_basis="lookup_snapshot",
                payload=payload,
                payload_sha256=digest(payload),
            )
        )
        start, end = (0, 180) if i == 1 else (180, 340)
        weather = {
            "format": "regional-weather-v1",
            "source_ref": f"synthetic-weather-{start}",
            "area_center": {"lat": 37.5, "lng": 127},
            "area_radius_m": 5000,
            "valid_from": (at + timedelta(seconds=start)).isoformat(),
            "valid_until": (at + timedelta(seconds=end)).isoformat(),
            "temperature_c": 23 if i == 1 else 22,
            "wind_mps": 2.1,
        }
        backgrounds.append(
            SavedBackground(
                id=f"environment-{i}",
                target=material_ref(record),
                provider="weather-observation",
                payload_schema="walk-entry-context-v1",
                policy_version="walk-entry-context-v1",
                query_point=record.anchor.point,
                tags=("environment",),
                status="known",
                retrieved_at=points[-1].at,
                temporal_basis="source_observation",
                payload=weather,
                payload_sha256=digest(weather),
            )
        )
    source = DiaryInput(
        owner_id="synthetic-owner",
        walk_id=str(walk_id),
        client_session_id="synthetic-session",
        started_at=at,
        ended_at=points[-1].at,
        pet_ids=(),
        evidence_origin="mock",
        route=route,
        records=tuple(records),
        photos_status="not_available",
        backgrounds=tuple(backgrounds),
        selected_background_ids=tuple(b.id for b in backgrounds),
        observations=build_observation_pool(evidence, route).observations,
        scene_policy_version="records-first-v1",
        writing_policy_version="diary-part-slots-v1",
    )
    return source, VerifiedBoardRoute(route, evidence), points
