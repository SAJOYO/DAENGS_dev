import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from daengs_backend.routers import facility_discovery as gateway
from daengs_backend.services.facility_conversation import FacilityConversationService
from tests.place.api import test_conversation as support
from tests.place.api.test_conversation import chat_body, manual_body
from tests.place.support.conversation import place

harness = support.harness


async def test_member_snapshot_is_private_complete_and_read_again_for_new_request(
    harness, monkeypatch
):
    client, _, searcher, calls, plans, _ = harness
    searcher.rows = [place(str(i), source="kcisa", distance=i + 1) for i in range(26)]
    saved = [p.key.model_dump() for p in searcher.rows[:20]]
    reads = []

    async def read(self, owner):
        assert owner == "owner-a"
        reads.append(owner)
        return saved

    monkeypatch.setattr(FacilityConversationService, "bookmark_keys", read)
    first = (await client.post("/app/places/conversation", json=manual_body())).json()
    plans.append(
        {"goal": "show", "search_scope": "new_candidates", "search_scope_quote": "새로운 곳"}
    )
    request = {**chat_body(first, "새로운 곳 보여줘"), "candidate_pools": "v1"}
    result = await client.post("/app/places/conversation", json=request)
    assert result.status_code == 200, result.text
    new = result.json()
    assert [k["ref"] for k in new["display_order"]] == [str(i) for i in range(20, 26)]
    assert new["search_pool"] == "new_candidates"
    assert "bookmark_keys" not in json.loads(calls[-1]["input"])
    assert "owner-a" not in json.dumps(calls)
    assert (await client.post("/app/places/conversation", json=request)).json() == new
    assert len(reads) == 1
    saved.clear()
    plans.append({"goal": "show"})
    changed = (
        await client.post(
            "/app/places/conversation",
            json={**chat_body(new, "다시 보여줘"), "candidate_pools": "v1"},
        )
    ).json()
    assert changed["display_order"][0]["ref"] == "0" and len(reads) == 2


async def test_missing_member_snapshot_retains_search_and_rejects_client_spoofing(
    harness, monkeypatch
):
    client, _, searcher, _, plans, _ = harness

    async def unavailable(self, owner):
        return None

    monkeypatch.setattr(FacilityConversationService, "bookmark_keys", unavailable)
    old = (await client.post("/app/places/conversation", json=manual_body())).json()
    plans.append(
        {"goal": "show", "search_scope": "new_candidates", "search_scope_quote": "새로운 곳"}
    )
    request = {**chat_body(old, "새로운 곳"), "candidate_pools": "v1"}
    response = (await client.post("/app/places/conversation", json=request)).json()
    assert response["receipt"]["code"] == "candidate_pool_unavailable"
    assert response["filters"] == old["filters"] and response["search"] == old["search"]
    assert len(searcher.calls) == 1
    for private in ("bookmark_keys", "restore_exploration", "previous"):
        assert (
            await client.post("/app/places/conversation", json={**request, private: []})
        ).status_code == 422


async def test_known_correction_survives_http_search_failure_and_owned_restore(
    harness, monkeypatch
):
    client, store, searcher, _, plans, app = harness

    async def read(self, owner):
        return []

    monkeypatch.setattr(FacilityConversationService, "bookmark_keys", read)
    old = (await client.post("/app/places/conversation", json=manual_body())).json()
    plans.append(
        {"goal": "show", "search_scope": "new_candidates", "search_scope_quote": "새로운 곳"}
    )
    new = (
        await client.post(
            "/app/places/conversation",
            json={**chat_body(old, "새로운 곳"), "candidate_pools": "v1"},
        )
    ).json()
    searcher.error = True
    plans.append(
        {
            "goal": "explain",
            "feedback": "familiarity",
            "familiarity": {
                "quote": "첫 번째 이미 알아",
                "targets": [{"kind": "ordinal", "text": "첫 번째"}],
            },
        }
    )
    failed = (
        await client.post(
            "/app/places/conversation",
            json={**chat_body(new, "첫 번째 이미 알아"), "candidate_pools": "v1"},
        )
    ).json()
    assert failed["receipt"]["execution"] == "failed"
    assert len(json.loads(store.items[old["session_id"]])["state"]["exploration"]["known"]) == 1
    searcher.error = False
    restore = {
        "client_request_id": str(uuid4()),
        "mode": "restore",
        "restore_filters": failed["filters"],
        "restore_pool": "new_candidates",
        "candidate_pools": "v1",
        "source_session_id": failed["session_id"],
        "source_revision": failed["revision"],
    }
    result = await client.post("/app/places/conversation", json=restore)
    assert result.status_code == 200, result.text
    assert result.json()["display_order"] == [new["display_order"][1]]
    stale = {**restore, "client_request_id": str(uuid4()), "source_revision": 1}
    assert (await client.post("/app/places/conversation", json=stale)).status_code == 409
    app.dependency_overrides[gateway.facility_owner] = lambda: "another-owner"
    assert (
        await client.post(
            "/app/places/conversation", json={**restore, "client_request_id": str(uuid4())}
        )
    ).status_code == 410


@pytest.mark.parametrize(
    "case", ["empty", "complete", "partial", "duplicate", "too_many", "failed"]
)
async def test_member_reader_accepts_only_complete_bounded_snapshots(monkeypatch, case):
    from sqlalchemy.exc import SQLAlchemyError

    from daengs_backend.core import database
    from daengs_backend.schemas.place_bookmark import BookmarkKey
    from daengs_backend.services import place_bookmark

    owner = uuid4()

    class Db:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    async def read(db, who):
        assert who == owner
        if case == "failed":
            raise SQLAlchemyError("private")
        keys = [] if case == "empty" else [BookmarkKey(source="kcisa", ref="A")]
        if case == "duplicate":
            keys *= 2
        if case == "too_many":
            keys = [BookmarkKey(source="kcisa", ref=str(i)) for i in range(201)]
        return SimpleNamespace(
            total_count=99 if case == "partial" else len(keys),
            items=[SimpleNamespace(key=k) for k in keys],
        )

    monkeypatch.setattr(database, "SessionLocal", Db)
    monkeypatch.setattr(place_bookmark, "list_saved", read)
    result = await FacilityConversationService().bookmark_keys(str(owner))
    if case in {"empty", "complete"}:
        assert result == ([] if case == "empty" else [{"source": "kcisa", "ref": "A"}])
    else:
        assert result is None
