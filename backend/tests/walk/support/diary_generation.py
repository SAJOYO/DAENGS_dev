"""Shared authenticated diary API fixture; real adapters and fake provider/DB boundaries."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.config import settings
from daengs_backend.core.database import get_session
from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
from daengs_backend.routers import walk_storyboard as router
from daengs_backend.services import walk_diary_input as reader
from daengs_backend.services import walk_diary_writing as writer
from daengs_backend.services import walk_storyboard as legacy
from daengs_backend.services.walk_diary_base_board import PreparedSavedBaseBoard
from daengs_backend.services.walk_diary_board_slot_writing import write_board
from daengs_walk.diary_input import digest
from daengs_walk.diary_scene_input import scene_materials
from tests.walk.support.diary import place_payload, prose
from tests.walk.support.observations import varied_route
from tests.walk.support.photo_input import OWNER, WALK, context_envelope, entry

PATH = f"/app/walks/{WALK}/storyboard"
FORMAT = "walk-diary-bundle-v1"
QUERY = f"?bundle_format={FORMAT}&target_scene_count=3"


@pytest.fixture
def api(monkeypatch):
    walk, analysis, _ = varied_route()
    envelope = context_envelope()
    payload = place_payload(("registered-place", "합성 카페", 60))
    envelope.update(status="known", payload=payload, payload_sha256=digest(payload))
    state = SimpleNamespace(
        row=None,
        walk=walk,
        analysis=analysis,
        entries=[entry()],
        photo=None,
        envelope=envelope,
        context_jobs={},
        before_write=None,
    )
    state.entries[0].mutation_id = uuid.uuid4()
    db = SimpleNamespace(
        new=set(),
        dirty=set(),
        deleted=set(),
        expire_all=Mock(),
        commit=AsyncMock(),
        add=lambda row: setattr(state, "row", row),
    )
    monkeypatch.setattr(
        reader.walks,
        "get_owned_for_update",
        AsyncMock(side_effect=lambda s, o, w: state.walk if o == OWNER and w == WALK else None),
    )
    monkeypatch.setattr(
        reader.storyboards, "latest_analysis", AsyncMock(side_effect=lambda *a: state.analysis)
    )
    monkeypatch.setattr(reader.entries, "entries", AsyncMock(side_effect=lambda *a: state.entries))
    monkeypatch.setattr(
        reader.pets, "accessible_ids", AsyncMock(side_effect=lambda *a: set(state.walk.pet_ids))
    )
    monkeypatch.setattr(reader.pets, "names_by_ids", AsyncMock(return_value={}))
    monkeypatch.setattr(reader.photos, "current", AsyncMock(side_effect=lambda *a: state.photo))
    monkeypatch.setattr(
        reader.contexts,
        "current",
        AsyncMock(
            side_effect=lambda _session, row, **kw: (
                state.context_jobs.get(row.id, []),
                {}
                if state.envelope is None
                else {"space.facility": SimpleNamespace(envelope=state.envelope)},
            )
        ),
    )
    monkeypatch.setattr(legacy.repo, "current", AsyncMock(side_effect=lambda *a: state.row))
    monkeypatch.setattr(legacy.repo, "reference_walks", AsyncMock(return_value=[]))
    monkeypatch.setattr(settings, "walk_diary_enabled", True)
    monkeypatch.setattr(settings, "walk_diary_space_enabled", False)
    monkeypatch.setattr(settings, "walk_entry_v2_enabled", False)
    monkeypatch.setattr(settings, "walk_photo_metadata_enabled", True)
    monkeypatch.setattr(settings, "walk_entry_context_enabled", True)

    async def generate(payload, schema):
        # The persisted reservation is visible before external work begins.
        assert db.commit.await_count >= 1 and state.row.status == "running"
        if state.before_write:
            await state.before_write()
        if payload.get("format") == "scene-and-optional-action-v1":
            return {
                "scenes": [
                    {
                        "scene_id": scene["scene_id"],
                        "text": "가까이에 등록된 카페가 있었다."
                        if scene["scene"]["where"]
                        else "이동 구간의 속도에 변화가 있었다.",
                        "evidence_ids": [e["id"] for e in scene_materials(scene)[:1]],
                        "action_id": scene["action"]["id"] if scene["action"] else None,
                    }
                    for scene in payload["scenes"]
                ]
            }
        return prose(payload)

    state.provider = AsyncMock(side_effect=generate)

    async def write(source, prepared):
        if isinstance(prepared, PreparedSavedBaseBoard):
            return await write_board(source, prepared, state.provider)
        return await writer.write_diary(source, prepared, state.provider)

    state.writer = AsyncMock(side_effect=write)
    app = FastAPI()
    app.include_router(router.router)
    app.dependency_overrides[get_session] = lambda: db
    app.dependency_overrides[CurrentAppUser.__metadata__[0].dependency] = lambda: AppPrincipal(
        app_user_id=OWNER
    )
    app.dependency_overrides[router.get_diary_writer] = lambda: state.writer
    state.lookup = AsyncMock(return_value={})
    app.dependency_overrides[router.get_context_lookup] = lambda: state.lookup
    app.dependency_overrides[router.get_title_generator] = lambda: AsyncMock(
        side_effect=lambda bundle: bundle
    )
    return TestClient(app), state, db


def body(state, **updates):
    return {
        "bundle_format": FORMAT,
        "target_scene_count": 3,
        "expected_entries": {str(e.id): e.revision for e in state.entries},
        "expected_photo_manifest": None
        if state.photo is None
        else {"publisher_id": str(state.photo.publisher_id), "revision": state.photo.revision},
        **updates,
    }
