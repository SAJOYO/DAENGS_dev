import json

import httpx
import pytest

from daengs_place.place.intent.contract import (
    IntentProposerInvalidOutputError,
    ProposalDisposition,
    ProposalReason,
    SearchModeId,
)
from daengs_place.place.providers.gemini import (
    GeminiIntentProposer,
    GeminiIntentProposerResponseError,
    GeminiIntentProposerTimeoutError,
)


def _completed(output: dict) -> dict:
    return {
        "status": "completed",
        "steps": [
            {
                "type": "model_output",
                "content": [{"type": "text", "text": json.dumps(output)}],
            }
        ],
    }


@pytest.mark.asyncio
async def test_interactions_request_preserves_evaluated_stateless_schema() -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1beta/interactions"
        assert "key" not in request.url.params
        assert request.headers["x-goog-api-key"] == "test-key"
        payload = json.loads(request.content)
        assert payload["model"] == "gemini-3.1-flash-lite"
        assert payload["input"] == "어디 갈까"
        assert payload["store"] is False
        assert payload["generation_config"] == {
            "max_output_tokens": 1800,
            "temperature": 0.0,
        }
        assert "반려견 동반 장소 검색" in payload["system_instruction"]
        assert (
            "required_target proposal이 하나라도 있으면 search_mode는 반드시 directed_search"
            in payload["system_instruction"]
        )
        output_format = payload["response_format"]
        assert output_format["type"] == "text"
        assert output_format["mime_type"] == "application/json"
        serialized_schema = json.dumps(output_format["schema"])
        assert "$ref" not in serialized_schema
        assert "$defs" not in serialized_schema
        return httpx.Response(
            200,
            json=_completed(
                {
                    "disposition": "abstained",
                    "interpretations": [],
                    "reason": "insufficient_target",
                }
            ),
        )

    proposer = GeminiIntentProposer(
        "test-key",
        "gemini-3.1-flash-lite",
        transport=httpx.MockTransport(handle),
    )

    output = await proposer.propose("어디 갈까")

    assert output.disposition is ProposalDisposition.ABSTAINED
    assert output.reason is ProposalReason.INSUFFICIENT_TARGET


@pytest.mark.asyncio
async def test_flat_provider_output_becomes_typed_intent_without_guessing_offsets() -> None:
    async def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_completed(
                {
                    "disposition": "proposed",
                    "reason": "none",
                    "interpretations": [
                        {
                            "search_mode": "directed_search",
                            "proposals": [
                                {
                                    "role": "required_target",
                                    "intent_type": "purpose",
                                    "purpose_id": "dining",
                                    "quote": "밥 먹을 곳",
                                    "start": 0,
                                }
                            ],
                        }
                    ],
                }
            ),
        )

    proposer = GeminiIntentProposer(
        "test-key",
        "model",
        transport=httpx.MockTransport(handle),
    )

    output = await proposer.propose("밥 먹을 곳")

    interpretation = output.interpretations[0]
    proposal = interpretation.proposals[0]
    assert interpretation.search_directive.mode is SearchModeId.DIRECTED_SEARCH
    assert proposal.intent.intent_type == "purpose"
    assert proposal.intent.purpose_id.value == "dining"
    assert proposal.evidence.start is None and proposal.evidence.end is None


@pytest.mark.asyncio
async def test_groundable_mode_only_open_discovery_is_preserved() -> None:
    async def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_completed(
                {
                    "disposition": "proposed",
                    "reason": "none",
                    "interpretations": [
                        {
                            "search_mode": "open_discovery",
                            "search_mode_quote": "네가 추천해봐",
                            "proposals": [],
                        }
                    ],
                }
            ),
        )

    proposer = GeminiIntentProposer(
        "test-key",
        "model",
        transport=httpx.MockTransport(handle),
    )

    output = await proposer.propose("오늘 심심한데 네가 추천해봐")

    interpretation = output.interpretations[0]
    assert interpretation.search_directive.mode is SearchModeId.OPEN_DISCOVERY
    assert interpretation.search_directive.evidence.quote == "네가 추천해봐"
    assert interpretation.proposals == ()


@pytest.mark.asyncio
async def test_abstention_discards_schema_forced_incomplete_proposals() -> None:
    async def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_completed(
                {
                    "disposition": "abstained",
                    "interpretations": [
                        {
                            "search_mode": "directed_search",
                            "proposals": [
                                {
                                    "role": "hypothetical",
                                    "intent_type": "semantic",
                                    "quote": "강아지가 좋아하는거 있는곳",
                                }
                            ],
                        }
                    ],
                    "reason": "insufficient_target",
                }
            ),
        )

    proposer = GeminiIntentProposer(
        "test-key",
        "model",
        transport=httpx.MockTransport(handle),
    )

    output = await proposer.propose("강아지가 좋아하는거 있는곳")

    assert output.disposition is ProposalDisposition.ABSTAINED
    assert output.reason is ProposalReason.INSUFFICIENT_TARGET
    assert output.interpretations == ()


@pytest.mark.asyncio
async def test_none_reason_becomes_unspecified_only_for_abstention() -> None:
    async def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_completed(
                {
                    "disposition": "abstained",
                    "interpretations": [],
                    "reason": "none",
                }
            ),
        )

    proposer = GeminiIntentProposer(
        "test-key",
        "model",
        transport=httpx.MockTransport(handle),
    )

    output = await proposer.propose("안녕")

    assert output.reason is ProposalReason.UNSPECIFIED
    assert output.interpretations == ()


@pytest.mark.asyncio
async def test_timeout_is_classified_without_provider_details() -> None:
    async def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret provider detail", request=request)

    proposer = GeminiIntentProposer(
        "test-key",
        "model",
        transport=httpx.MockTransport(timeout),
    )

    with pytest.raises(GeminiIntentProposerTimeoutError) as captured:
        await proposer.propose("카페")

    assert str(captured.value) == "Gemini interaction timed out"
    assert "secret" not in str(captured.value)


@pytest.mark.asyncio
async def test_http_and_envelope_failures_are_classified() -> None:
    async def http_failure(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="sensitive upstream body")

    proposer = GeminiIntentProposer(
        "test-key",
        "model",
        transport=httpx.MockTransport(http_failure),
    )
    with pytest.raises(GeminiIntentProposerResponseError) as captured:
        await proposer.propose("카페")
    assert "sensitive" not in str(captured.value)

    async def invalid_body(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json")

    proposer = GeminiIntentProposer(
        "test-key",
        "model",
        transport=httpx.MockTransport(invalid_body),
    )
    with pytest.raises(GeminiIntentProposerResponseError, match="not valid JSON"):
        await proposer.propose("카페")

    async def incomplete(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "incomplete", "steps": []})

    proposer = GeminiIntentProposer(
        "test-key",
        "model",
        transport=httpx.MockTransport(incomplete),
    )
    with pytest.raises(GeminiIntentProposerResponseError, match="did not complete"):
        await proposer.propose("카페")


@pytest.mark.asyncio
async def test_invalid_structured_output_is_not_salvaged() -> None:
    async def invalid(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_completed(
                {
                    "disposition": "proposed",
                    "reason": "none",
                    "interpretations": [
                        {
                            "proposals": [
                                {
                                    "role": "required_target",
                                    "intent_type": "semantic",
                                    "quote": "조용한 곳",
                                }
                            ]
                        }
                    ],
                }
            ),
        )

    proposer = GeminiIntentProposer(
        "test-key",
        "model",
        transport=httpx.MockTransport(invalid),
    )

    with pytest.raises(IntentProposerInvalidOutputError, match="invalid intent payload"):
        await proposer.propose("조용한 곳")
