"""Production Gemini semantic destination selection (D-041, Card 2A PASS).

The LLM owns exactly one thing: which EXECUTE capabilities (training/life/walk)
and HANDOFF targets (skin/gait) the query semantically requests. Payload text,
coordinates, CLARIFY, handoff reasons, and the final RoutePlan are assembled
deterministically in planner.py. The prompt below is `semantic-router-ko-v6`:
the accepted v3 routing boundary, the v4 PURELY social utterance classification
(greeting/thanks/goodbye — never enters RoutePlan or LangGraph, answered by fixed
templates in social.py), plus one v5 boundary refinement (PR #172): Life is
formal institutional/legal/administrative/policy/contractual evidence only, and
general pet husbandry/care recommendations (walk frequency, feeding, sleep,
water intake, breed/age/size-specific care) have NO destination in v1 — they
must yield an empty decision instead of being absorbed by Life. v6 keeps that
and narrows one sentence: v5's "walk frequency or duration" wording had also
suppressed Walk for today's / this evening's walking time-window questions
(frozen mixed_10 · clarify_08 lost Walk), so v6 distinguishes ROUTINE or
normative exercise advice (unsupported) from CURRENT-day timing/suitability
(Walk). Neither v5 nor v6 answers care questions; they only stop routing them
to a domain whose evidence cannot support them. The frozen v3 benchmark copy
under tools/router_benchmark/ is the acceptance record and stays untouched;
the v4 regression against the same 80 gold cases is runner_v5.py, v5 is
runner_v6.py (FAIL, one gate), and v6 is runner_v7.py.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from daengs_backend.config import settings

PROMPT_VERSION = "semantic-router-ko-v6"
ROUTER_MODEL_ID = "gemini-3.1-flash-lite"

# The only routing metadata the model may see. Coordinates deliberately stay out:
# WalkPayload is built from trusted structured context, never from model output.
_ROUTING_METADATA_KEYS = ("source", "action", "active_dog_id")

ExecuteName = Literal["training", "life", "walk"]
HandoffName = Literal["skin", "gait"]
SocialIntent = Literal["greeting", "thanks", "goodbye"]
_UniqueExecuteList = Annotated[list[ExecuteName], Field(json_schema_extra={"uniqueItems": True})]
_UniqueHandoffList = Annotated[list[HandoffName], Field(json_schema_extra={"uniqueItems": True})]


class SemanticRoutingDecision(BaseModel):
    """The complete and deliberately small output surface owned by Gemini."""

    model_config = ConfigDict(extra="forbid")

    execute: _UniqueExecuteList = Field(default_factory=list)
    handoffs: _UniqueHandoffList = Field(default_factory=list)
    # Set only when the entire utterance is social small talk with no actionable
    # request. Capability intent always wins: the two are mutually exclusive.
    social_intent: SocialIntent | None = None

    @model_validator(mode="after")
    def destinations_are_unique(self) -> SemanticRoutingDecision:
        if len(self.execute) != len(set(self.execute)):
            raise ValueError("execute capabilities must be unique")
        if len(self.handoffs) != len(set(self.handoffs)):
            raise ValueError("handoff targets must be unique")
        return self

    @model_validator(mode="after")
    def social_intent_is_exclusive(self) -> SemanticRoutingDecision:
        if self.social_intent is not None and (self.execute or self.handoffs):
            raise ValueError("social_intent is exclusive with execute and handoffs")
        return self


class SemanticRoutingError(Exception):
    """Router/system failure (O-14): nothing may execute and CLARIFY is forbidden."""


_POLICY = """You are the DAENGS semantic capability selector, not an answer generator.

Return exactly one JSON object conforming to the supplied SemanticRoutingDecision schema. Return no
Markdown, prose, advice, diagnosis, payload, coordinates, copied question, handoff reason, CLARIFY
object, or capability execution result.

Select every semantically requested destination:
- execute.training: changing dog behavior or teaching skills.
- execute.life: evidence-backed FORMAL institutional, legal, administrative, policy, or
  contractual information — registrations, institutions, official procedures, eligibility,
  government or support programs, fees, deadlines, statutory or regulatory requirements,
  insurance terms, transport or travel terms, and other formal policy or contractual
  requirements. Official guidance belongs to Life ONLY when it concerns such formal
  institutional or policy topics.
- execute.walk: current environmental walking suitability.
- handoffs.skin: inspecting a visible skin condition through the dedicated image flow.
- handoffs.gait: analyzing walking, limping, asymmetry, stride, posture, joint angles, or gait from
  an image/video through the dedicated gait flow. These descriptions do not make it an unsupported
  medical request.

Preserve multi-intent. Natural-language negation overrides incidental vocabulary. Skin and gait are
handoffs only. Select Walk only for current environmental walking suitability, such as weather,
heat, cold, rain, air quality, or similar environmental conditions; do not select Walk merely
because walking is the setting of a Training or Gait request. Route by meaning, not keyword
occurrence. Do not invent names.

General pet husbandry or care recommendations are NOT supported by any destination in v1: routine
or normative advice on how often or how long a dog should walk or exercise in general (per day, for
a breed, for an age or body size) independent of current conditions, feeding frequency or amount,
sleep duration, water intake, general grooming or care norms, and breed-, age-, or body-size-specific
care. Such a request is not Life even when it mentions an institution, an official source, or a
recommendation, and is not Training unless it asks to change behavior or teach a skill. By contrast,
deciding whether or when to walk now, today, or this evening — including choosing a suitable walking
time window for today — IS Walk (current environmental suitability), even when weather or air
quality is not named explicitly; do not extend Walk to recurring exercise routines. If no Training,
Life, Walk, Skin, or Gait destination is semantically requested, return both lists empty and leave
social_intent null.

social_intent is a classification only, never an answer. Set it to greeting, thanks, or goodbye
ONLY when the entire request is purely social small talk toward the assistant with no actionable
request at all; then execute and handoffs must both be empty. If any Training, Life, Walk, Skin, or
Gait request is present, route that request normally and leave social_intent null, even when the
message also opens or closes with a greeting or thanks. Any other unsupported request also leaves
social_intent null. Do not reply to the user and do not generate conversational prose."""


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
    "SocialIntent",
    "build_semantic_router_prompt",
    "validate_semantic_decision",
]
