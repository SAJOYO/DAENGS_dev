"""PC-R01..R03: controlled HTTP events, using the catalog's cafe/restaurant fixtures."""

import asyncio
import json

from daengs_evals.place_conversation.fixtures import FixtureSearcher
from daengs_evals.place_conversation.runner import DATA, read_cases
from daengs_place.api import conversation_internal
from daengs_place.place.conversation.contract import TurnPlan
from tests.place.api import test_conversation as support
from tests.place.api.test_conversation import (
    answer_body,
    chat_body,
    manual_body,
    recovery_body,
)

harness = support.harness

ENDPOINT = "/app/places/conversation"
PARKING_PLAN = TurnPlan(
    goal="show",
    changes={
        "upsert_all": [
            {
                "id": "parking",
                "capability": "operations.parking",
                "op": "eq",
                "value": True,
            }
        ]
    },
)


async def start(harness, case_id):
    client, _, searcher, _, _, _ = harness
    case = next(c for c in read_cases(DATA / "cases.v1.jsonl") if c["id"] == case_id)
    fixtures = json.loads((DATA / "fixtures.v1.json").read_text(encoding="utf-8"))
    searcher.rows = FixtureSearcher(case["setup"], fixtures).rows
    body = manual_body()
    body["manual"]["kinds"] = case["setup"]["candidate_kinds"]
    response = await client.post(ENDPOINT, json=body)
    assert response.status_code == 200
    return response.json()


async def test_pc_r01_manual_none_wins_over_late_parking(harness, monkeypatch, record_property):
    client, store, _, _, _, _ = harness
    initial = await start(harness, "PC-R01")
    entered, release = asyncio.Event(), asyncio.Event()

    class Slow:
        async def plan(self, request):
            entered.set()
            await release.wait()
            return PARKING_PLAN

    monkeypatch.setattr(conversation_internal, "provider", lambda: Slow())
    pending = asyncio.create_task(client.post(ENDPOINT, json=chat_body(initial, "주차 필수야")))
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        manual = manual_body(initial)
        manual["manual"]["kinds"] = ["cafe", "restaurant"]
        latest = await client.post(ENDPOINT, json=manual)
        assert latest.status_code == 200
    finally:
        release.set()
    old = await pending
    saved = json.loads(store.items[initial["session_id"]])
    assert old.status_code == 409
    assert saved["state"]["filters"]["hard"]["all"] == []
    assert saved["revision"] == latest.json()["revision"] == 2
    record_property("scenario_id", "PC-R01")
    record_property(
        "trace",
        json.dumps(
            {
                "old_http_status": old.status_code,
                "latest": latest.json(),
                "saved": saved,
            },
            ensure_ascii=False,
        ),
    )


async def test_pc_r02_failed_answer_preserves_commit(harness, monkeypatch, record_property):
    client, _, searcher, _, _, _ = harness
    initial = await start(harness, "PC-R02")

    class BrokenAnswer:
        async def plan(self, request):
            return TurnPlan(goal="pick_one")

        async def answer(self, request):
            raise TimeoutError("controlled provider failure")

    monkeypatch.setattr(conversation_internal, "provider", lambda: BrokenAnswer())
    committed = (await client.post(ENDPOINT, json=chat_body(initial, "하나 골라줘"))).json()
    calls = len(searcher.calls)
    answered = await client.post(ENDPOINT + "/answer", json=answer_body(committed))
    assert answered.status_code == 200
    final = answered.json()
    assert final["answer"]["source"] == "fallback"
    for field in ("revision", "filters", "search", "selected", "receipt"):
        assert final[field] == committed[field]
    assert len(searcher.calls) == calls
    recovered = (await client.post(ENDPOINT + "/recover", json=recovery_body(final))).json()
    assert recovered == final
    record_property("scenario_id", "PC-R02")
    record_property(
        "trace",
        json.dumps(
            {
                "committed": committed,
                "answered": final,
                "additional_search_calls": 0,
            },
            ensure_ascii=False,
        ),
    )


async def test_pc_r03_lost_reply_reuses_identity_then_recovers_latest(harness, record_property):
    client, _, searcher, calls, plans, _ = harness
    initial = await start(harness, "PC-R03")
    plans.append(PARKING_PLAN.model_dump(mode="json", exclude_unset=True))
    request = chat_body(initial, "주차되는 곳만 보여줘")
    # The response is observed by the harness, discarded from the simulated client.
    committed = (await client.post(ENDPOINT, json=request)).json()
    counts = (len(searcher.calls), len(calls))
    retried = await client.post(ENDPOINT, json=request)
    assert retried.json() == committed
    assert (len(searcher.calls), len(calls)) == counts
    manual = manual_body(committed)
    manual["manual"]["kinds"] = ["cafe", "restaurant"]
    manual["manual"]["radius_m"] = 1000
    latest = (await client.post(ENDPOINT, json=manual)).json()
    counts = (len(searcher.calls), len(calls))
    stale = await client.post(ENDPOINT, json=request)
    assert stale.status_code == 409
    recovered = (await client.post(ENDPOINT + "/recover", json=recovery_body(committed))).json()
    assert recovered == latest
    assert (len(searcher.calls), len(calls)) == counts
    record_property("scenario_id", "PC-R03")
    record_property(
        "trace",
        json.dumps(
            {
                "committed": committed,
                "retried": retried.json(),
                "stale_http_status": 409,
                "recovered": recovered,
                "duplicate_search_calls": 0,
                "duplicate_model_calls": 0,
            },
            ensure_ascii=False,
        ),
    )
