import json

import httpx
import pytest

from daengs_place.place.filters.proposer import (
    GeminiFilterProposer,
    _gemini_edit_schema,
    _resolve_evidence,
)
from daengs_place.place.providers.gemini import (
    GeminiIntentProposerResponseError,
    GeminiIntentProposerTimeoutError,
)
from tests.place.place.filters.test_edits import atom, base, edit, proposal


def test_unique_exact_quote_resolves_korean_and_emoji_offsets_without_changing_meaning():
    raw = edit("주차", atom=atom())
    raw["evidence"].update(start=3, end=6)
    result = _resolve_evidence("🐕 주차 가능", proposal(raw))
    assert (result.edits[0].evidence.start, result.edits[0].evidence.end) == (2, 4)
    assert result.edits[0].atom.id == "parking"
    assert result.edits[0].evidence.origin == "explicit"


@pytest.mark.parametrize("query", ["주차 주차", "주 차", "주차장은 있어"])
def test_missing_or_repeated_quote_is_not_guessed(query):
    raw = edit("주차" if query != "주차장은 있어" else "주차 가능", atom=atom())
    raw["evidence"].update(start=1, end=2)
    with pytest.raises(ValueError, match="unresolvable_evidence"):
        _resolve_evidence(query, proposal(raw))


def test_valid_span_can_select_a_specific_repeated_quote():
    raw = edit("주차", atom=atom())
    raw["evidence"].update(start=3, end=5)
    original = proposal(raw)
    assert _resolve_evidence("주차 주차", original) == original


def test_provider_schema_is_finite_and_keeps_types_and_unknown_field_guards():
    schema = _gemini_edit_schema()
    encoded = json.dumps(schema)
    assert "$ref" not in encoded and "$defs" not in encoded
    assert "maxItems" not in encoded and "maxLength" not in encoded
    operation = schema["properties"]["edits"]["items"]
    assert operation["additionalProperties"] is False
    assert "upsert_branch" in operation["properties"]["type"]["enum"]
    assert operation["properties"]["atom"]["anyOf"][0]["properties"]["value"]["anyOf"][0] == {
        "type": "boolean"
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"edits": [edit("주차", atom=atom("x" * 101))], "unresolved": []},
        {"edits": [edit("주차", atom=atom())] * 13, "unresolved": []},
        {"edits": [edit("주차", atom=atom(value="true"))], "unresolved": []},
        {
            "edits": [edit("주차", type="remove_all", target_id="parking", atom=atom())],
            "unresolved": [],
        },
        {"edits": [edit("주차", type="set_name_query", name_query=None)], "unresolved": []},
    ],
)
async def test_provider_projection_does_not_relax_domain_limits(payload):
    body = {
        "status": "completed",
        "steps": [
            {"type": "model_output", "content": [{"type": "text", "text": json.dumps(payload)}]}
        ],
    }
    proposer = GeminiFilterProposer(
        "key", "model", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))
    )
    with pytest.raises(GeminiIntentProposerResponseError):
        await proposer.propose("주차", base())


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
