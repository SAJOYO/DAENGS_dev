"""Production Gemini semantic destination selection (D-041, Card 2A PASS).

The LLM owns exactly one thing: which EXECUTE capabilities (training/life/walk)
and HANDOFF targets (skin/gait) the query semantically requests. Payload text,
coordinates, CLARIFY, handoff reasons, and the final RoutePlan are assembled
deterministically in planner.py. The prompt below is the accepted
`semantic-router-ko-v3` boundary; the frozen benchmark copy under
tools/router_benchmark/ is the acceptance record and stays untouched.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from daengs_backend.config import settings

PROMPT_VERSION = "semantic-router-ko-v3"
ROUTER_MODEL_ID = "gemini-3.5-flash-lite"

# The only routing metadata the model may see. Coordinates deliberately stay out:
# WalkPayload is built from trusted structured context, never from model output.
_ROUTING_METADATA_KEYS = ("source", "action", "active_dog_id")

ExecuteName = Literal["training", "life", "walk"]
HandoffName = Literal["skin", "gait"]
_UniqueExecuteList = Annotated[list[ExecuteName], Field(json_schema_extra={"uniqueItems": True})]
_UniqueHandoffList = Annotated[list[HandoffName], Field(json_schema_extra={"uniqueItems": True})]


class SemanticRoutingDecision(BaseModel):
    """The complete and deliberately small output surface owned by Gemini."""

    model_config = ConfigDict(extra="forbid")

    execute: _UniqueExecuteList = Field(default_factory=list)
    handoffs: _UniqueHandoffList = Field(default_factory=list)

    @model_validator(mode="after")
    def destinations_are_unique(self) -> SemanticRoutingDecision:
        if len(self.execute) != len(set(self.execute)):
            raise ValueError("execute capabilities must be unique")
        if len(self.handoffs) != len(set(self.handoffs)):
            raise ValueError("handoff targets must be unique")
        return self


class SemanticRoutingError(Exception):
    """Router/system failure (O-14): nothing may execute and CLARIFY is forbidden."""


_POLICY = """You are the DAENGS semantic capability selector, not an answer generator.

Return exactly one JSON object conforming to the supplied SemanticRoutingDecision schema. Return no
Markdown, prose, advice, diagnosis, payload, coordinates, copied question, handoff reason, CLARIFY
object, or capability execution result.

Select every semantically requested destination:
- execute.training: changing dog behavior or teaching skills.
- execute.life: evidence-backed information about rules, institutions, procedures, conditions,
  fees, deadlines, official guidance, insurance terms, or pet travel rules.
- execute.walk: current environmental walking suitability.
- handoffs.skin: inspecting a visible skin condition through the dedicated image flow.
- handoffs.gait: analyzing walking, limping, asymmetry, stride, posture, joint angles, or gait from
  an image/video through the dedicated gait flow. These descriptions do not make it an unsupported
  medical request.

Preserve multi-intent. Natural-language negation overrides incidental vocabulary. Skin and gait are
handoffs only. Select Walk only for current environmental walking suitability, such as weather,
heat, cold, rain, air quality, or similar environmental conditions; do not select Walk merely
because walking is the setting of a Training or Gait request. Route by meaning, not keyword
occurrence. Do not invent names. If no supported destination is semantically requested, return both
lists empty."""


def build_semantic_router_prompt(*, query: str, context: dict[str, Any]) -> str:
    if not query.strip():
        raise ValueError("query must not be blank")
    # Card 2A validated routing metadata as non-empty strings; malformed internal
    # context (a dict/list/blank under an approved key) must never reach the prompt.
    metadata: dict[str, str] = {}
    for key in _ROUTING_METADATA_KEYS:
        if key not in context:
            continue
        value = context[key]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"routing metadata {key} must be a non-empty string")
        metadata[key] = value
    schema = json.dumps(
        SemanticRoutingDecision.model_json_schema(), ensure_ascii=False, sort_keys=True
    )
    return (
        f"PROMPT_VERSION: {PROMPT_VERSION}\n\n"
        f"{_POLICY}\n\n"
        f"SEMANTIC_DECISION_JSON_SCHEMA:\n{schema}\n\n"
        f"INPUT_LOCALE: ko-KR\n"
        f"ROUTING_METADATA: {json.dumps(metadata, ensure_ascii=False, sort_keys=True)}\n"
        f"USER_QUERY: {query}\n"
    )


def validate_semantic_decision(raw: object) -> SemanticRoutingDecision | None:
    """Schema-validate provider output; the invalid raw output is never surfaced."""
    parsed: object = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if isinstance(parsed, SemanticRoutingDecision):
        return parsed
    try:
        return SemanticRoutingDecision.model_validate(parsed)
    except (ValidationError, ValueError, TypeError):
        return None


@lru_cache(maxsize=1)
def _gemini_client() -> Any:
    # google-genai stays a function-local import so importing the router never
    # pulls provider machinery (mirrors the lazy-import rule for heavy stacks).
    from google import genai
    from google.genai import types

    api_key = settings.gemini_api_key.get_secret_value().strip()
    if not api_key:
        raise SemanticRoutingError("GEMINI_API_KEY is required for the semantic router")
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=settings.gemini_timeout_ms),
    )


async def _generate_with_gemini(prompt: str) -> object:
    def _call() -> object:
        from google.genai import types

        response = _gemini_client().models.generate_content(
            model=ROUTER_MODEL_ID,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.0,
                candidate_count=1,
                max_output_tokens=256,
                response_mime_type="application/json",
                response_json_schema=SemanticRoutingDecision.model_json_schema(),
            ),
        )
        parsed = getattr(response, "parsed", None)
        return parsed if parsed is not None else getattr(response, "text", None)

    return await asyncio.to_thread(_call)


class GeminiSemanticRouter:
    """Strict structured-output selection with the O-14 single schema retry."""

    def __init__(self, generate: Callable[[str], Awaitable[object]] | None = None) -> None:
        self._generate = generate or _generate_with_gemini

    async def select(self, *, query: str, context: dict[str, Any]) -> SemanticRoutingDecision:
        prompt = build_semantic_router_prompt(query=query, context=context)
        for _attempt in range(2):  # O-14: retry exactly once, only on schema failure
            try:
                raw = await self._generate(prompt)
            except SemanticRoutingError:
                raise
            except Exception as exc:  # a provider failure is a router failure, never CLARIFY
                raise SemanticRoutingError("semantic router provider call failed") from exc
            decision = validate_semantic_decision(raw)
            if decision is not None:
                return decision  # schema-valid output is final; a misroute is never retried
        raise SemanticRoutingError("semantic router output failed schema validation twice")


__all__ = [
    "PROMPT_VERSION",
    "ROUTER_MODEL_ID",
    "GeminiSemanticRouter",
    "SemanticRoutingDecision",
    "SemanticRoutingError",
    "build_semantic_router_prompt",
    "validate_semantic_decision",
]
