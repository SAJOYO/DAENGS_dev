from tests.place.api.test_conversation import (  # noqa: F401
    chat_body,
    harness,
    manual_body,
    recovery_body,
)
from tests.place.support.conversation import place


async def test_gateway_negotiates_app_execution_and_recovery_does_not_claim_saved(harness):  # noqa: F811
    client, _, searcher, _, plans, _ = harness
    searcher.rows = [
        place(p.key.ref, kind=p.match.kind, distance=p.distance_m, source="kcisa")
        for p in searcher.rows
    ]
    before = (await client.post("/app/places/conversation", json=manual_body())).json()
    plans.append(
        {
            "goal": "edit_only",
            "bookmark": {
                "operation": "save",
                "operation_quote": "찜해줘",
                "target": {"kind": "selected", "text": "여기"},
            },
        }
    )
    body = {
        **chat_body(before, "여기 찜해줘"),
        "bookmark_commands": "v1",
        "visible_selected": before["display_order"][0],
    }
    response = await client.post("/app/places/conversation", json=body)
    assert response.status_code == 200
    result = response.json()
    assert result["receipt"]["bookmark_command"]["key"] == before["display_order"][0]
    assert result["answer_status"] == "none" and result["answer"] is None
    assert result["search"] == before["search"] and result["filters"] == before["filters"]
    assert (await client.post("/app/places/conversation", json=body)).json() == result
    assert (
        await client.post("/app/places/conversation/recover", json=recovery_body(result))
    ).json() == result
    assert len(searcher.calls) == 1
