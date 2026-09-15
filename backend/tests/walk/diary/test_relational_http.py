"""Authenticated HTTP -> real relational orchestration -> saved receipt -> GET."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from daengs_backend.routers import walk_storyboard as router
from daengs_backend.schemas.walk_relational_diary import RELATIONAL_FORMAT, RELATIONAL_STORAGE
from daengs_backend.services.walk_diary.lifecycle import relational
from daengs_backend.services.walk_diary.runtime import write_relational_board
from tests.walk.diary.test_brief_execution import answer
from tests.walk.diary.test_brief_execution import prepare as brief_prepare  # noqa: F401
from tests.walk.diary.test_relational_orchestration import (
    prepare,  # noqa: F401 -- fixture registration
    public_collector,  # noqa: F401 -- fixture registration
    send,
)
from tests.walk.support.diary_generation import PATH, body
from tests.walk.support.photo_input import OWNER, WALK

QUERY = f"?bundle_format={RELATIONAL_FORMAT}&target_scene_count=3"


@pytest.fixture(params=["v7", "v8"])
def relational_api(api, prepare, brief_prepare, monkeypatch, request):  # noqa: F811
    client, state, db = api
    state.relational_calls = 0
    state.relational_hook = None
    state.relational_failure = None
    state.publication_version = request.param
    original_store = relational.store_result

    def store(*args, **kwargs):
        try:
            return original_store(*args, **kwargs)
        except Exception as exc:
            state.relational_failure = str(exc)
            raise

    monkeypatch.setattr(relational, "store_result", store)

    async def write(source, base, **kwargs):
        assert db.commit.await_count and state.row.status == "running"
        state.relational_calls += 1
        if state.relational_hook:
            await state.relational_hook()
        try:
            return await write_relational_board(
                source,
                base,
                prepare=brief_prepare if request.param == "v8" else prepare,
                send=brief_send if request.param == "v8" else send,
                execution_policy=replace(kwargs["execution_policy"], minimum_interval_s=0),
            )
        except Exception as exc:
            state.relational_failure = str(exc)
            raise

    client.app.dependency_overrides[router.get_diary_writer] = lambda: write
    return client, state, db


async def brief_send(stage, payload, schema):
    return answer(stage, payload)


def spec(state, **updates):
    return body(state, bundle_format=RELATIONAL_FORMAT, **updates)


def test_http_stores_canonical_parts_and_get_never_replans(relational_api, monkeypatch):
    client, state, _ = relational_api
    result = client.post(PATH, json=spec(state))
    assert result.status_code == 200, result.text
    value = result.json()
    assert value["status"] == "ready", (value, state.relational_failure)
    assert state.row.bundle["format"] == RELATIONAL_STORAGE
    assert state.row.bundle["payload"]["receipt"]["version"].endswith(state.publication_version)
    assert value["bundle"]["cards"]
    for card in value["bundle"]["cards"]:
        assert card["body"] == "\n".join(
            card[k]["text"] for k in ("space", "action") if card[k]["text"]
        )
    assert any(c["originals"] for c in value["bundle"]["cards"])
    assert "prepared" not in value and "raw_text" not in str(value)

    def no_plan(*args):
        raise AssertionError("GET must not prepare a new board")

    monkeypatch.setattr(relational, "assemble_relational_base", no_plan)
    assert client.get(PATH + QUERY).json() == value
    assert client.post(PATH, json=spec(state)).json() == value
    assert state.relational_calls == 1
    assert (
        client.get(PATH + "?bundle_format=walk-diary-board-v1&target_scene_count=3").status_code
        == 409
    )
    assert client.post(PATH, json=body(state)).status_code == 409


def test_background_completion_does_not_invalidate_but_original_edit_does(relational_api):
    client, state, _ = relational_api

    async def enrich():
        state.envelope = None  # A different background snapshot during writing.

    state.relational_hook = enrich
    value = client.post(PATH, json=spec(state)).json()
    assert value["status"] == "ready", value
    state.entries[0].revision += 1
    state.entries[0].payload = {**state.entries[0].payload, "note": "새 원문"}
    stale = client.get(PATH + QUERY).json()
    assert stale["status"] == "stale" and stale["bundle"] is None


def test_original_edit_during_generation_prevents_publication(relational_api):
    client, state, _ = relational_api

    async def change():
        state.entries[0].revision += 1
        state.entries[0].payload = {**state.entries[0].payload, "note": "수정한 기록"}

    state.relational_hook = change
    value = client.post(PATH, json=spec(state)).json()
    assert value["status"] == "stale" and value["bundle"] is None
    assert state.row.status == "running"


def test_reservation_survives_old_60_second_lease_and_get_expiry_writes_no_prose(relational_api):
    client, state, _ = relational_api

    async def inspect():
        row = state.row
        assert relational.active(row, row.updated_at + timedelta(seconds=70))
        deadline = datetime.fromisoformat(row.bundle["deadline_at"])
        assert not relational.active(row, deadline)
        assert relational.active(row, deadline - timedelta(seconds=1))
        limits = row.bundle["execution_limits"]
        assert (
            limits["generation_seconds"]
            >= limits["max_model_calls"] * 15 + max(0, limits["max_model_calls"] - 1) * 10
        )
        assert (deadline - row.updated_at).total_seconds() == limits["generation_seconds"] + 12 + 15

    state.relational_hook = inspect
    assert client.post(PATH, json=spec(state)).json()["status"] == "ready"
    state.row.status = "running"
    state.row.bundle = {
        "format": relational.RELATIONAL_PENDING,
        "deadline_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
    }
    before = deepcopy(state.row.bundle)
    value = client.get(PATH + QUERY).json()
    assert value["status"] == "failed" and value["bundle"] is None
    assert state.row.bundle == before and state.row.status == "running"


def test_corrupted_saved_receipt_is_not_displayed(relational_api):
    client, state, _ = relational_api
    assert client.post(PATH, json=spec(state)).json()["status"] == "ready"
    state.row.bundle["payload"]["public"]["cards"][0]["body"] = "다른 글"
    result = client.get(PATH + QUERY).json()
    assert result["status"] == "failed" and result["bundle"] is None


@pytest.mark.parametrize("relational_api", ["v7"], indirect=True)
def test_old_dong_only_publication_still_reads(relational_api):
    from daengs_walk.value_contracts import digest

    client, state, _ = relational_api
    value = client.post(PATH, json=spec(state)).json()
    assert value["status"] == "ready"
    saved = state.row.bundle["payload"]
    for card in saved["receipt"]["cards"]:
        card["comparison"]["header"].pop("administrative_address", None)
    for bundle in (saved["public"], value["bundle"]):
        for card in bundle["cards"]:
            card["header"].pop("administrative_address", None)
    state.row.bundle["digest"] = digest(saved)
    assert client.get(PATH + QUERY).json() == value


async def test_newer_generation_wins_completion(relational_api):
    from daengs_backend.schemas.walk_generation import StoryboardRequest

    client, state, db = relational_api
    writer = client.app.dependency_overrides[router.get_diary_writer]()

    async def replace_generation():
        state.row.generation += 1

    state.relational_hook = replace_generation
    result = await relational.generate_relational(
        db,
        OWNER,
        WALK,
        StoryboardRequest.model_validate(spec(state)),
        writer=writer,
    )
    assert result.status == "running" and result.generation == 2
    assert result.bundle is None
