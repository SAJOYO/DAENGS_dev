"""Shared diary test builders; no test cases."""

from daengs_walk.diary.contracts.input import (
    DiaryInput,
    MovementObservation,
    SavedBackground,
    UserRecord,
    digest,
    material_ref,
)


def record(id="note", minute="05", *, photo=False, unlocated=False):
    at = f"2026-09-09T00:{minute}:00Z"
    return UserRecord.model_validate(
        {
            "ref": {
                "id": id,
                "store": "walk_photo" if photo else "walk_entry",
                "version": "1",
                "version_kind": "revision",
                "pin_revision": 1,
            },
            "content": {"kind": "photo", "media_ref": "app-private-photo:photo"}
            if photo
            else {"kind": "note", "text": "  두부와 사진을 찍었다.\n다음 기록도 남김  "},
            "anchor": {
                "event_at": at,
                "time_basis": "photo_capture" if photo else "recorded_at",
                "point": None if unlocated else {"lat": 37.5, "lng": 127.0},
                "location_at": None if unlocated else "2026-09-09T00:04:55Z",
                "position_state": "unlocated" if unlocated else "resolved",
                "method": "none" if unlocated else "last_known",
            },
        }
    )


def source(*records, **updates):
    return DiaryInput.model_validate(
        {
            "owner_id": "owner",
            "walk_id": "server-walk",
            "client_session_id": "phone-walk",
            "started_at": "2026-09-09T00:00:00Z",
            "ended_at": "2026-09-09T00:40:00Z",
            "pet_ids": ["dog"],
            "evidence_origin": "device",
            "route": {
                "status": "ready",
                "analysis_id": "analysis",
                "input_fingerprint": "a" * 64,
                "calculation_version": 1,
            },
            "records": list(records),
            "photos_status": "complete",
            "scene_policy_version": "records-first-v1",
            "writing_policy_version": "diary-background-v1",
            **updates,
        }
    )


def observation():
    at = "2026-09-09T00:20:00Z"
    return MovementObservation.model_validate(
        {
            "id": "dwell",
            "version": "b" * 64,
            "analysis_id": "analysis",
            "kind": "observed_dwell",
            "started_at": at,
            "ended_at": "2026-09-09T00:21:00Z",
            "anchor": {
                "event_at": at,
                "time_basis": "route_observation",
                "location_at": at,
                "point": {"lat": 37.51, "lng": 127.01},
                "position_state": "resolved",
                "method": "observed",
                "source_fixes": [{"client_seq": 20, "chain_index": 0, "at": at}],
            },
        }
    )


def place_payload(*items):
    return {
        "geometry": "registered_location",
        "visit_confirmed": False,
        "radius_m": 250,
        "coverage": "selected_place_categories_only",
        "items": [
            {
                "kind": "cafe",
                "place": {
                    "name": name,
                    "key": {"source": "fixture", "ref": id},
                    "distance_m": distance,
                },
            }
            for id, name, distance in items
        ],
    }


def nearby(core, id="bg", *, payload=None, status="known", **updates):
    payload = payload if payload is not None else place_payload(("a", "합성 카페", 60))
    return SavedBackground(
        id=id,
        target=material_ref(core),
        provider="place-search",
        payload_schema="walk-entry-context-v1",
        policy_version="walk-entry-context-v1",
        query_point=core.anchor.point,
        tags=("space",),
        status=status,
        retrieved_at="2026-09-09T02:00:00Z",
        temporal_basis="lookup_snapshot",
        payload=payload,
        payload_sha256=digest(payload),
        **updates,
    )


def with_backgrounds(*records, backgrounds=(), **updates):
    return source(
        *records,
        backgrounds=backgrounds,
        selected_background_ids=[b.id for b in backgrounds if b.status in {"known", "partial"}],
        **updates,
    )


def prose(payload, schema=None):
    return {
        "title": "함께 남긴 산책",
        "scenes": [
            {
                "scene_id": s["scene_id"],
                "text": "등록된 카페가 가까이에 있었다.",
                "evidence_ids": [s["background_ids"][0]],
            }
            for s in payload["scenes"]
        ],
    }
