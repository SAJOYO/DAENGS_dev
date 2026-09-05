"""Real chunk decode + measurement + geo scenes across the authenticated HTTP boundary."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
from daengs_backend.models.walk_entry import WalkEntry
from daengs_backend.routers import walk_storyboard as router
from daengs_backend.schemas.walk import WalkPointUpload
from daengs_backend.services import walk_storyboard as service
from daengs_backend.services.walk_chunk import encode_chunk
from daengs_backend.services.walk_finalize import walk_input_fingerprint
from daengs_backend.services.walk_storyboard_context import lookup_contexts

OWNER, WALK, SESSION, ENTRY = [uuid.uuid4() for _ in range(4)]
START = datetime(2026, 9, 5, tzinfo=UTC)


@pytest.fixture
def live(monkeypatch):
    points = [
        WalkPointUpload(
            client_seq=i,
            chain_index=0,
            at=START + timedelta(seconds=i * 10),
            lat=37.5,
            lng=127 + i * 0.000113,
            accuracy_m=5,
        )
        for i in range(121)
    ]
    chunk = SimpleNamespace(seq_from=0, seq_to=120, point_count=121, payload=encode_chunk(points))
    walk = SimpleNamespace(
        id=WALK,
        client_session_id=SESSION,
        pet_ids=[],
        started_at=START,
        ended_at=START + timedelta(seconds=1200),
        points=[chunk],
        analysis_state="derived",
    )
    analysis = SimpleNamespace(
        id=uuid.uuid4(),
        point_count=121,
        terminal_client_seq=120,
        input_fingerprint=walk_input_fingerprint(points),
    )
    state = SimpleNamespace(row=None, entries=[], walk=walk)
    db = SimpleNamespace(
        add=lambda row: setattr(state, "row", row), commit=AsyncMock(), expire_all=lambda: None
    )
    monkeypatch.setattr(
        service.walks,
        "get_owned_for_update",
        AsyncMock(side_effect=lambda s, o, w: walk if o == OWNER and w == WALK else None),
    )
    monkeypatch.setattr(service.repo, "latest_analysis", AsyncMock(return_value=analysis))
    monkeypatch.setattr(service.repo, "current", AsyncMock(side_effect=lambda s, w: state.row))
    monkeypatch.setattr(
        service.entries_repo, "entries", AsyncMock(side_effect=lambda s, ws: state.entries)
    )
    lookup = AsyncMock(
        side_effect=lambda selection: {
            a["id"]: {
                "facts": ["등록 카페 주변 (테스트 자료)"],
                "sources": [
                    {
                        "source": "test-place",
                        "status": "known",
                        "captured_at": START.isoformat(),
                        "source_url": None,
                    }
                ],
            }
            for a in selection["anchors"]
        }
    )
    app = FastAPI()
    app.include_router(router.router)
    app.dependency_overrides[get_session] = lambda: db
    app.dependency_overrides[CurrentAppUser.__metadata__[0].dependency] = lambda: AppPrincipal(
        app_user_id=OWNER
    )
    app.dependency_overrides[router.get_context_lookup] = lambda: lookup
    return TestClient(app), state, lookup


PATH = f"/app/walks/{WALK}/storyboard"


def note(revision=1, content="메모"):
    return WalkEntry(
        walk_id=WALK,
        id=ENTRY,
        revision=revision,
        mutation_id=uuid.uuid4(),
        payload={
            "kind": "note",
            "behavior_code": None,
            "note": content,
            "recorded_at": (START + timedelta(seconds=450)).isoformat(),
            "location": None,
            "pet_id": None,
        }
        if content is not None
        else None,
    )


def test_pinless_real_observations_generate_and_cache(live):
    client, _state, lookup = live
    assert client.get(PATH).json()["status"] == "pending"
    first = client.post(PATH, json={"expected_entries": {}})
    assert first.status_code == 200, first.text
    result = first.json()
    assert result["status"] == "ready", result
    assert result["bundle"]["synthetic"] is False
    assert result["bundle"]["session_id"] == str(SESSION)
    assert any("distance_fill" in s["reasons"] for s in result["bundle"]["scenes"])
    assert any(f["kind"] == "environment" for s in result["bundle"]["scenes"] for f in s["facts"])
    assert client.post(PATH, json={"expected_entries": {}}).json() == result
    assert client.get(PATH).json() == result
    assert lookup.await_count == 1


def test_environment_deadline_keeps_scenes_and_can_refresh(live, monkeypatch):
    client, _, lookup = live
    monkeypatch.setattr(service, "CONTEXT_TIMEOUT_SECONDS", 0.01)
    cancelled = []

    async def slow(_):
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.append(True)

    successful_lookup = lookup.side_effect
    lookup.side_effect = slow
    response = client.post(PATH, json={"expected_entries": {}})
    assert response.status_code == 200
    fallback = response.json()
    assert fallback["status"] == "ready" and fallback["error_code"] is None
    scenes = fallback["bundle"]["scenes"]
    assert {"start", "end"} <= {s["id"] for s in scenes}
    anchors = [s for s in scenes if "distance_fill" in s["reasons"]]
    assert anchors and all(any(f["kind"] == "coverage" for f in s["facts"]) for s in anchors)
    assert all(
        any(
            s["provider"] == "place-search" and s["status"] == "unavailable" and s["captured_at"]
            for s in a["sources"]
        )
        for a in anchors
    )
    assert cancelled == [True]
    # Normal retry reuses the successful observation bundle; explicit refresh retries environment.
    assert client.post(PATH, json={"expected_entries": {}}).json() == fallback
    assert lookup.await_count == 1
    lookup.side_effect = successful_lookup
    refreshed = client.post(PATH, json={"expected_entries": {}, "refresh": True}).json()
    assert refreshed["generation"] > fallback["generation"]
    assert any(
        f["kind"] == "environment" for s in refreshed["bundle"]["scenes"] for f in s["facts"]
    )


def test_environment_deadline_does_not_publish_obsolete_input(live, monkeypatch):
    client, state, lookup = live
    monkeypatch.setattr(service, "CONTEXT_TIMEOUT_SECONDS", 0.01)

    async def mutate_then_wait(_):
        state.entries = [note()]
        await asyncio.sleep(60)

    lookup.side_effect = mutate_then_wait
    response = client.post(PATH, json={"expected_entries": {}}).json()
    assert response["status"] == "stale" and response["bundle"] is None


async def test_request_cancellation_is_not_an_environment_fallback(live):
    client, state, lookup = live
    from daengs_backend.schemas.walk_storyboard import StoryboardRequest

    lookup.side_effect = asyncio.CancelledError
    db = client.app.dependency_overrides[get_session]()
    with pytest.raises(asyncio.CancelledError):
        await service.generate(db, OWNER, WALK, StoryboardRequest(expected_entries={}), lookup)
    assert state.row.status == "running" and state.row.bundle is None


def test_measurement_timeout_is_still_an_analysis_failure(live, monkeypatch):
    client, _, lookup = live

    def broken(*args):
        raise TimeoutError("measurement failure")

    monkeypatch.setattr(service, "analyze_walk", broken)
    response = client.post(PATH, json={"expected_entries": {}}).json()
    assert response["status"] == "failed" and response["bundle"] is None
    lookup.assert_not_awaited()


def test_correction_deletion_and_source_ids(live):
    client, state, _ = live
    state.entries = [note()]
    first = client.post(PATH, json={"expected_entries": {str(ENTRY): 1}}).json()
    old = next(s for s in first["bundle"]["scenes"] if s["id"] == f"entry:{ENTRY}")
    state.entries = [note(2, "정정")]
    assert client.get(PATH).json()["status"] == "stale"
    assert client.get(PATH).json()["bundle"] is None
    assert client.post(PATH, json={"expected_entries": {str(ENTRY): 1}}).status_code == 409
    changed = client.post(PATH, json={"expected_entries": {str(ENTRY): 2}}).json()
    new = next(s for s in changed["bundle"]["scenes"] if s["id"] == old["id"])
    assert new["revision"] != old["revision"] and new["route"] is None
    state.entries = [note(3, None)]
    removed = client.post(PATH, json={"expected_entries": {str(ENTRY): 3}}).json()
    assert not any(s["id"] == old["id"] for s in removed["bundle"]["scenes"])
    assert removed["generation"] > changed["generation"] > first["generation"]


def test_late_response_cannot_replace_newer_source(live):
    client, state, lookup = live

    async def change(_):
        state.entries = [note()]
        return {}

    lookup.side_effect = change
    result = client.post(PATH, json={"expected_entries": {}}).json()
    assert result["status"] == "stale" and result["bundle"] is None
    assert state.row.status == "running"  # Old computation wasn't published.


def test_running_lease_failure_retry_and_ownership(live):
    client, state, lookup = live
    lookup.side_effect = RuntimeError("provider details must not escape")
    failed = client.post(PATH, json={"expected_entries": {}}).json()
    assert failed["status"] == "failed" and failed["error_code"] == "scene_analysis_failed"
    lookup.side_effect = lambda _: {}
    ready = client.post(PATH, json={"expected_entries": {}}).json()
    assert ready["status"] == "ready" and ready["generation"] == failed["generation"] + 1
    state.row.status = "running"
    running = client.post(PATH, json={"expected_entries": {}, "refresh": True}).json()
    assert running["status"] == "running" and running["generation"] == ready["generation"]
    state.row.updated_at -= timedelta(seconds=61)
    resumed = client.post(PATH, json={"expected_entries": {}}).json()
    assert resumed["status"] == "ready" and resumed["generation"] == ready["generation"] + 1
    assert client.get(f"/app/walks/{uuid.uuid4()}/storyboard").status_code == 404
    state.walk.analysis_state = "collecting"
    assert client.post(PATH, json={"expected_entries": {}}).status_code == 409


async def test_place_lookup_is_bounded_and_failure_is_unknown():
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await lookup_contexts(
            {"anchors": [{"id": str(i), "location": {"lat": 37.5, "lng": 127}} for i in range(8)]},
            client=client,
        )
    assert len(calls) == 8
    assert all(
        x["facts"] == [] and x["sources"][0]["status"] == "unavailable" for x in result.values()
    )


async def test_place_lookup_projects_factual_source_not_a_visit():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "groups": [
                    {
                        "kind": "cafe",
                        "truncated": True,
                        "results": [
                            {
                                "place": {
                                    "name": "카페",
                                    "distance_m": 80,
                                    "key": {"source": "kto", "ref": "42"},
                                }
                            }
                        ],
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await lookup_contexts(
            {"anchors": [{"id": "a", "location": {"lat": 37.5, "lng": 127}}]}, client=client
        )
    assert "kto:42" in result["a"]["facts"][0]
    assert "방문/내부 판정 아님" in result["a"]["facts"][0]
    assert result["a"]["sources"][0]["status"] == "partial"


def test_history_uses_observed_speed_and_retains_reference_sources(live):
    from daengs_backend.schemas.walk import WalkFinalizeRequest
    from daengs_backend.services.walk_finalize import prepare_finalized_walk
    from daengs_walk import analyze_walk
    from daengs_walk.storyboard import build_storyboard
    from daengs_walk.storyboard_input import scene_inputs

    _, state, _ = live
    histories = [
        SimpleNamespace(
            id=uuid.uuid4(),
            client_session_id=uuid.uuid4(),
            started_at=state.walk.started_at,
            ended_at=state.walk.ended_at,
            points=state.walk.points,
        )
        for _ in range(3)
    ]
    references = service.history_references(histories, "pet")
    assert len(references) == 3 and all(r.median_speed_mps > 0 for r in references)
    # Shift the comparison dates into the past; a slower current session is a recorded difference.
    references = [
        r.model_copy(update={"started_at": START - timedelta(days=i + 1), "median_speed_mps": 4.0})
        for i, r in enumerate(references)
    ]
    prepared = prepare_finalized_walk(
        state.walk.points, WalkFinalizeRequest(expected_point_count=121, terminal_client_seq=120)
    )
    evidence = analyze_walk(WALK, START, state.walk.ended_at, prepared.points)
    entries, selection = scene_inputs(
        evidence, [], session_id=str(SESSION), pet_id="pet", references=references
    )
    assert selection["reference_status"] == "available"
    bundle = build_storyboard(
        str(SESSION),
        START,
        state.walk.ended_at,
        evidence.facts.moving_distance_m,
        entries,
        selection,
        {},
    )
    scene = next(s for s in bundle.scenes if "profile_change" in s.reasons)
    assert len([s for s in scene.sources if s.provider == "walk-history"]) == 3
