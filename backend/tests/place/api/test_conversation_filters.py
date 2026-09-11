"""Direct filter removal across HTTP, committed exploration and pending AI changes."""

import asyncio
import json
from uuid import uuid4

import pytest

from daengs_place.api import conversation_internal
from daengs_place.place.conversation.intent import PendingDecision
from tests.place.api import test_conversation as support
from tests.place.support.conversation import place

harness = support.harness
URL = "/app/places/conversation"


def filters_body(previous, **removals):
    return {
        "client_request_id": str(uuid4()),
        "mode": "filters",
        "session_id": previous["session_id"],
        "expected_revision": previous["revision"],
        "remove_filters": removals,
    }


async def turn(client, body):
    response = await client.post(URL, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def saved_state(store, response):
    return json.loads(store.items[response["session_id"]])["state"]


async def filtered_search(harness):
    client, _, _, _, plans, _ = harness
    first = await turn(client, support.manual_body())
    plans.append(
        {
            "goal": "show",
            "changes": {
                "parking": "required_true",
                "alternatives": [{"kinds": ["shopping"]}, {"kinds": ["pet_shop"]}],
            },
        }
    )
    result = await turn(client, support.chat_body(first, "주차되는 쇼핑시설이나 애견용품점"))
    assert result["filters"]["hard"]["all"] and result["filters"]["hard"]["any"]
    return result


def parking_id(response):
    return next(
        atom["id"]
        for atom in response["filters"]["hard"]["all"]
        if atom["capability"] == "operations.parking"
    )


async def exclude_first(harness, previous, *, next_page=False):
    client, _, _, _, plans, _ = harness
    plans.append(
        {
            "goal": "show",
            "browse": "next" if next_page else "current",
            "place_edit": {
                "operation": "exclude",
                "operation_quote": "빼고",
                "targets": [{"kind": "ordinal", "text": "첫 번째"}],
            },
        }
    )
    query = "첫 번째 빼고 더 보여줘" if next_page else "첫 번째 빼고 보여줘"
    excluded = await turn(client, support.chat_body(previous, query))
    assert excluded["receipt"]["excluded_places"]
    return excluded


async def test_removal_preserves_other_conditions_and_excluded_places_without_model(harness):
    client, store, searcher, calls, _, _ = harness
    searcher.rows.append(place("no-parking", parking=False))
    filtered = await filtered_search(harness)
    excluded = await exclude_first(harness, filtered)
    previous = saved_state(store, excluded)
    request = filters_body(excluded, remove_all=[parking_id(excluded)])
    relaxed = await turn(client, request)
    assert relaxed["filters"]["hard"]["all"] == []
    assert relaxed["filters"]["hard"]["any"] == excluded["filters"]["hard"]["any"]
    for field in ("candidate_kinds", "spatial", "name_query", "dogs", "preferences"):
        assert relaxed["filters"][field] == excluded["filters"][field]
    assert [p["ref"] for p in relaxed["display_order"]] == ["no-parking", "second"]
    assert (
        saved_state(store, relaxed)["exploration"]["excluded"]
        == previous["exploration"]["excluded"]
    )
    assert saved_state(store, relaxed)["history"] == previous["history"]
    assert relaxed["answer"] is None and relaxed["answer_status"] == "none"
    assert len(calls) == 2 and len(searcher.calls) == 4
    assert await turn(client, request) == relaxed
    assert (await client.post(URL + "/answer", json=support.answer_body(relaxed))).json() == relaxed
    assert (
        await client.post(URL + "/recover", json=support.recovery_body(relaxed))
    ).json() == relaxed
    assert len(searcher.calls) == 4 and len(calls) == 2
    ungrouped = await turn(
        client,
        filters_body(relaxed, remove_any=[b["id"] for b in relaxed["filters"]["hard"]["any"]]),
    )
    assert ungrouped["filters"]["hard"] == {"all": [], "any": []}
    assert ungrouped["display_order"] == relaxed["display_order"]
    assert len(calls) == 2 and len(searcher.calls) == 5


async def test_removing_or_group_preserves_global_parking_condition(harness):
    client, _, searcher, calls, _, _ = harness
    searcher.rows.append(place("no-parking", parking=False))
    filtered = await filtered_search(harness)
    ungrouped = await turn(
        client,
        filters_body(filtered, remove_any=[b["id"] for b in filtered["filters"]["hard"]["any"]]),
    )
    assert ungrouped["filters"]["hard"]["all"] == filtered["filters"]["hard"]["all"]
    assert ungrouped["filters"]["hard"]["any"] == []
    assert [p["ref"] for p in ungrouped["display_order"]] == ["first", "second"]
    assert len(calls) == 1


async def test_failed_removal_preserves_filters_results_and_exploration_until_retry(harness):
    client, store, searcher, calls, _, _ = harness
    filtered = await filtered_search(harness)
    excluded = await exclude_first(harness, filtered)
    previous = saved_state(store, excluded)
    searcher.error = True
    failed = await turn(client, filters_body(excluded, remove_all=[parking_id(excluded)]))
    assert failed["receipt"]["execution"] == "failed"
    assert failed["filters"] == excluded["filters"] and failed["search"] == excluded["search"]
    for field in ("filters", "snapshot", "exploration", "history"):
        assert saved_state(store, failed)[field] == previous[field]
    searcher.error = False
    completed = await turn(client, filters_body(failed, remove_all=[parking_id(failed)]))
    assert completed["filters"]["hard"]["all"] == []
    assert completed["revision"] == failed["revision"] + 1 and len(calls) == 2
    assert [p["ref"] for p in completed["display_order"]] == ["second"]


async def test_stale_and_invalid_removals_leave_state_intact_and_empty_request_searches(harness):
    client, store, searcher, calls, plans, _ = harness
    first = await turn(client, support.manual_body())
    plans.append({"goal": "edit_only", "changes": {"radius_m": 1000}})
    edited = await turn(client, support.chat_body(first, "검색하지 말고 반경만 1km로 바꿔"))
    assert not edited["receipt"]["result_matches_filters"] and len(searcher.calls) == 1
    assert (await client.post(URL, json=filters_body(first))).status_code == 409
    previous = store.items[edited["session_id"]]
    for bad in ({"remove_all": ["missing"]}, {"remove_all": ["same", "same"]}, {"upsert_all": []}):
        assert (await client.post(URL, json=filters_body(edited, **bad))).status_code == 422
        assert store.items[edited["session_id"]] == previous
    refreshed = await turn(client, filters_body(edited))
    assert refreshed["filters"] == edited["filters"]
    assert refreshed["receipt"]["result_matches_filters"]
    assert len(searcher.calls) == 2 and len(calls) == 1


@pytest.mark.parametrize(
    "override",
    [
        {"session_id": None, "expected_revision": 0},
        {"remove_filters": None},
        {"manual": {}},
        {"query": "주차 빼줘"},
        {"restore_filters": {}},
        {"mode": "chat", "query": "주차 빼줘"},
        {"mode": "manual", "manual": support.manual_body()["manual"]},
    ],
)
async def test_removal_requires_existing_session_and_removal_only_envelope(harness, override):
    client, store, searcher, calls, _, _ = harness
    first = await turn(client, support.manual_body())
    previous = dict(store.items)
    assert (await client.post(URL, json={**filters_body(first), **override})).status_code == 422
    assert store.items == previous and len(searcher.calls) == 1 and calls == []


async def test_empty_removal_after_next_page_exhaustion_reopens_current_pool(harness):
    client, store, searcher, calls, plans, _ = harness
    searcher.rows = [place(str(i), distance=i + 1) for i in range(26)]
    first = await turn(client, support.manual_body())
    second = await exclude_first(harness, first, next_page=True)
    plans.append({"goal": "show", "browse": "next"})
    exhausted = await turn(client, support.chat_body(second, "더 보여줘"))
    assert exhausted["display_order"] == [] and exhausted["receipt"]["remaining"] == "exhausted"
    current = await turn(client, filters_body(exhausted))
    assert [p["ref"] for p in current["display_order"]] == [str(i) for i in range(1, 21)]
    assert current["filters"] == exhausted["filters"]
    assert saved_state(store, current)["exploration"]["excluded"][0]["key"]["ref"] == "0"
    assert len(searcher.calls) == 4 and len(calls) == 2


@pytest.mark.parametrize("inflight", [False, True])
async def test_direct_removal_cancels_pending_proposal_and_late_consent(
    harness, monkeypatch, inflight
):
    client, store, _, calls, plans, _ = harness
    filtered = await filtered_search(harness)
    plans.append({"goal": "show", "unsupported": ["quiet"], "changes": {"radius_m": 1000}})
    pending = await turn(client, support.chat_body(filtered, "조용한 곳으로 반경 1km만 찾아줘"))
    assert pending["receipt"]["action"] == "await_confirmation"
    entered, release = asyncio.Event(), asyncio.Event()

    class SlowConsent:
        async def decide_pending(self, request):
            entered.set()
            await release.wait()
            return PendingDecision(decision="accept")

    if inflight:
        monkeypatch.setattr(conversation_internal, "provider", lambda: SlowConsent())
        accepting = asyncio.create_task(client.post(URL, json=support.chat_body(pending, "응")))
    try:
        if inflight:
            await asyncio.wait_for(entered.wait(), timeout=3)
        latest = await turn(client, filters_body(pending, remove_all=[parking_id(pending)]))
    finally:
        release.set()
    if inflight:
        assert (await accepting).status_code == 409
    saved = saved_state(store, latest)
    assert saved["pending_proposal"] is None and saved["pending_question"] == ""
    assert latest["filters"]["hard"]["all"] == []
    assert latest["filters"]["spatial"]["radius_m"] == 3000
    assert saved["filters"] == latest["filters"] and saved["revision"] == latest["revision"]
    consent = await turn(client, support.chat_body(latest, "응"))
    assert consent["receipt"]["code"] == "no_pending_proposal"
    assert consent["filters"] == latest["filters"] and len(calls) == 2
