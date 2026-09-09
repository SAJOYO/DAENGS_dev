import json

import httpx
import pytest

from daengs_place.place.filters.proposer import GeminiFilterProposer
from daengs_place.place.providers.gemini import (
    GeminiIntentProposerResponseError,
    GeminiIntentProposerTimeoutError,
)
from tests.place.place.filters.test_edits import atom, base, edit, proposal


async def test_stateless_structured_provider_omits_location_and_profiles():
    expected = proposal(edit("주차", atom=atom()))

    def handle(request):
        body = json.loads(request.content)
        assert body["store"] is False and "previous_interaction_id" not in body
        assert body["response_format"]["schema"]["additionalProperties"] is False
        context = json.loads(body["input"])
        assert set(context["current"]) == {"candidate_kinds", "name_query", "hard", "preferences"}
        assert "sql_column" not in body["input"]
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "steps": [
                    {
                        "type": "model_output",
                        "content": [{"type": "text", "text": expected.model_dump_json()}],
                    }
                ],
            },
        )

    proposer = GeminiFilterProposer("test-key", "model", transport=httpx.MockTransport(handle))
    assert await proposer.propose("주차", base(dogs=[{"ref": "private"}])) == expected


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "incomplete"},
        {"status": "completed", "steps": []},
        {
            "status": "completed",
            "steps": [
                {
                    "type": "model_output",
                    "content": [
                        {"type": "text", "text": '{"edits":[],"unresolved":[],"locked":false}'}
                    ],
                }
            ],
        },
    ],
)
async def test_invalid_provider_response_is_not_salvaged(payload):
    proposer = GeminiFilterProposer(
        "key", "model", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    )
    with pytest.raises(GeminiIntentProposerResponseError):
        await proposer.propose("주차", base())


async def test_provider_timeout_is_distinct():
    def handle(request):
        raise httpx.ReadTimeout("timeout")

    with pytest.raises(GeminiIntentProposerTimeoutError):
        await GeminiFilterProposer("key", "model", transport=httpx.MockTransport(handle)).propose(
            "주차", base()
        )
