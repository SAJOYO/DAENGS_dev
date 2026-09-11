"""Actual saved-input adapter with mocked storage; no live DB/provider calls."""

import uuid
from unittest.mock import AsyncMock

from daengs_backend.config import settings
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.routers import walk_diary_slots as router
from tests.walk.support.photo_input import WALK

PATH = f"/app/walks/{WALK}/diary-slots/preview"


def wire(api):
    client, state, db = api
    client.app.include_router(router.router)
    db.rollback = AsyncMock()

    async def write(preview):
        assert db.commit.await_count == 1  # Walk/user read locks released before external work.
        assert state.row is None  # No generation reservation or publication.
        assert preview.stamps[1].evidence[0].part == "space"
        return preview

    writer = AsyncMock(side_effect=write)
    client.app.dependency_overrides[router.get_slot_writer] = lambda: writer
    return client, state, db, writer


def test_saved_preview_releases_transaction_before_writer(api):
    client, state, db, writer = wire(api)
    response = client.post(PATH, json={"target_scene_count": 3})
    assert response.status_code == 200, response.text
    preview = response.json()["preview"]
    assert preview["format"] == "walk-diary-slots-preview-v1"
    assert preview["base_board"]["input_revision"]
    assert writer.await_count == 1
    assert state.row is None
    db.rollback.assert_not_awaited()


def test_rules_only_never_calls_writer(api):
    client, _, _, writer = wire(api)
    response = client.post(PATH, json={"target_scene_count": 3, "generate": False})
    assert response.status_code == 200
    writer.assert_not_awaited()


def test_foreign_walk_does_not_call_writer(api):
    client, _, db, writer = wire(api)
    response = client.post(
        f"/app/walks/{uuid.uuid4()}/diary-slots/preview", json={"target_scene_count": 3}
    )
    assert response.status_code == 404
    writer.assert_not_awaited()
    db.rollback.assert_awaited_once()


def test_auth_feature_flag_and_client_source_injection(api, monkeypatch):
    client, _, _, writer = wire(api)
    assert client.post(PATH, json={"target_scene_count": 3, "source": {}}).status_code == 422
    monkeypatch.setattr(settings, "walk_diary_enabled", False)
    assert client.post(PATH, json={"target_scene_count": 3}).status_code == 404
    client.app.dependency_overrides.pop(CurrentAppUser.__metadata__[0].dependency)
    assert client.post(PATH, json={"target_scene_count": 3}).status_code == 401
    writer.assert_not_awaited()
