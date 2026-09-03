"""Gemini Interactions API adapter for Place intent proposals.

The evaluated Geo transport remains stateless structured output. Provider metadata and raw output
never enter the Place discovery HTTP contract.
"""

import json

import httpx

from daengs_place.place.intent.contract import (
    IntentProposerInvalidOutputError,
    LLMIntentOutput,
    ProposalDisposition,
    ProposalReason,
)
from daengs_place.place.intent.prompt import gemini_output_schema, proposer_instructions

GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_GENERATION_CONFIG = {
    "max_output_tokens": 1800,
    "temperature": 0.0,
}


class GeminiIntentProposerError(RuntimeError):
    """Provider transport or response envelope failed without exposing provider content."""


class GeminiIntentProposerTimeoutError(GeminiIntentProposerError):
    """The provider did not answer within the Place-owned deadline."""


class GeminiIntentProposerResponseError(GeminiIntentProposerError):
    """The provider returned an HTTP or interaction envelope failure."""


class GeminiIntentProposer:
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        base_url: str = GEMINI_API_BASE_URL,
        timeout_s: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if not api_key.strip():
            raise ValueError("Gemini API key is required")
        if not model.strip():
            raise ValueError("Gemini model is required")
        if timeout_s <= 0:
            raise ValueError("Gemini timeout must be positive")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._transport = transport

    async def propose(self, utterance: str) -> LLMIntentOutput:
        if not utterance.strip():
            raise ValueError("utterance must not be blank")
        payload = {
            "model": self._model,
            "input": utterance,
            "system_instruction": proposer_instructions()
            + "\nGemini adapter 출력에서는 intent를 평면 필드로 쓴다. intent_type에 맞는 "
            "kind, purpose_id, capability_id와 value, concept_id, activity_id, object_id 중 "
            "하나만 채워라. evidence는 quote와 확신할 때만 start/end로 출력하라. 각 "
            "interpretation에 search_mode를 출력하고, required_target proposal이 하나라도 "
            "있으면 search_mode는 반드시 directed_search다. open_discovery일 때만 "
            "search_mode_quote와 선택적인 search_mode_start/end를 출력하라. proposed이면 "
            "reason은 none, abstained이면 실제 abstention reason을 출력하라. 세부 사유를 "
            "고를 수 없으면 abstained와 unspecified를 출력하라.",
            "store": False,
            "response_format": {
                "type": "text",
                "mime_type": "application/json",
                "schema": gemini_output_schema(),
            },
            "generation_config": GEMINI_GENERATION_CONFIG,
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_s,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    f"{self._base_url}/interactions",
                    headers={
                        "x-goog-api-key": self._api_key,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise GeminiIntentProposerTimeoutError("Gemini interaction timed out") from exc
        except httpx.HTTPError as exc:
            raise GeminiIntentProposerResponseError("Gemini interaction request failed") from exc

        try:
            body = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise GeminiIntentProposerResponseError(
                "Gemini response body is not valid JSON"
            ) from exc
        if not isinstance(body, dict):
            raise GeminiIntentProposerResponseError("Gemini response must be an object")
        if body.get("status") != "completed":
            raise GeminiIntentProposerResponseError("Gemini interaction did not complete")
        output_text = _interaction_output_text(body)
        try:
            return _adapter_output(output_text)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise IntentProposerInvalidOutputError(
                "Gemini returned an invalid intent payload",
                raw_output=output_text,
            ) from exc


def _interaction_output_text(body: object) -> str:
    if not isinstance(body, dict):
        raise GeminiIntentProposerResponseError("Gemini response must be an object")
    chunks: list[str] = []
    steps = body.get("steps")
    if not isinstance(steps, list):
        raise GeminiIntentProposerResponseError("Gemini response has no steps")
    for step in steps:
        if not isinstance(step, dict) or step.get("type") != "model_output":
            continue
        content = step.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if (
                isinstance(part, dict)
                and part.get("type") == "text"
                and isinstance(part.get("text"), str)
            ):
                chunks.append(part["text"])
    if not chunks:
        raise GeminiIntentProposerResponseError("Gemini response has no output text")
    return "".join(chunks)


def _adapter_output(output_text: str) -> LLMIntentOutput:
    raw = json.loads(output_text)
    if not isinstance(raw, dict):
        raise TypeError("Gemini adapter output must be an object")
    _reject_unknown_fields(raw, {"disposition", "interpretations", "reason"})
    if "reason" not in raw:
        raise ValueError("Gemini adapter output requires a reason sentinel")
    reason = raw.get("reason")
    if reason == "none":
        reason = (
            ProposalReason.UNSPECIFIED.value
            if raw.get("disposition") == ProposalDisposition.ABSTAINED.value
            else None
        )
    if raw.get("disposition") == ProposalDisposition.ABSTAINED.value:
        return LLMIntentOutput.model_validate(
            {
                "disposition": raw.get("disposition"),
                "interpretations": [],
                "reason": reason,
            }
        )
    interpretations = []
    for interpretation in raw.get("interpretations", []):
        if not isinstance(interpretation, dict):
            raise TypeError("Gemini interpretation must be an object")
        _reject_unknown_fields(
            interpretation,
            {
                "search_mode",
                "search_mode_quote",
                "search_mode_start",
                "search_mode_end",
                "proposals",
            },
        )
        directive_start = interpretation.get("search_mode_start")
        directive_end = interpretation.get("search_mode_end")
        if (directive_start is None) != (directive_end is None):
            directive_start = None
            directive_end = None
        directive_quote = interpretation.get("search_mode_quote")
        directive_evidence = (
            {
                "quote": directive_quote,
                "start": directive_start,
                "end": directive_end,
            }
            if directive_quote is not None
            else None
        )
        proposals = []
        for proposal in interpretation.get("proposals", []):
            if not isinstance(proposal, dict):
                raise TypeError("Gemini proposal must be an object")
            _reject_unknown_fields(
                proposal,
                {
                    "role",
                    "intent_type",
                    "kind",
                    "purpose_id",
                    "capability_id",
                    "value",
                    "concept_id",
                    "activity_id",
                    "object_id",
                    "quote",
                    "start",
                    "end",
                },
            )
            intent_type = proposal.get("intent_type")
            if intent_type == "kind":
                intent = {"intent_type": intent_type, "kind": proposal.get("kind")}
            elif intent_type == "purpose":
                intent = {
                    "intent_type": intent_type,
                    "purpose_id": proposal.get("purpose_id"),
                }
            elif intent_type == "boolean_capability":
                intent = {
                    "intent_type": intent_type,
                    "capability_id": proposal.get("capability_id"),
                    "value": proposal.get("value"),
                }
            elif intent_type == "semantic":
                intent = {
                    "intent_type": intent_type,
                    "concept_id": proposal.get("concept_id"),
                }
            elif intent_type == "activity":
                intent = {
                    "intent_type": intent_type,
                    "activity_id": proposal.get("activity_id"),
                }
            elif intent_type == "object":
                intent = {
                    "intent_type": intent_type,
                    "object_id": proposal.get("object_id"),
                }
            else:
                raise ValueError(f"unknown Gemini intent type: {intent_type}")
            start = proposal.get("start")
            end = proposal.get("end")
            if (start is None) != (end is None):
                start = None
                end = None
            proposals.append(
                {
                    "role": proposal.get("role"),
                    "intent": intent,
                    "evidence": {
                        "quote": proposal.get("quote"),
                        "start": start,
                        "end": end,
                    },
                }
            )
        interpretations.append(
            {
                "search_directive": {
                    "mode": interpretation.get("search_mode"),
                    "evidence": directive_evidence,
                },
                "proposals": proposals,
            }
        )
    return LLMIntentOutput.model_validate(
        {
            "disposition": raw.get("disposition"),
            "interpretations": interpretations,
            "reason": reason,
        }
    )


def _reject_unknown_fields(value: dict, allowed: set[str]) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError("unknown Gemini adapter fields: " + ", ".join(unknown))
