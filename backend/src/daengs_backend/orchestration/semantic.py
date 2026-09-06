"""Production Gemini semantic destination selection (D-041, Card 2A PASS).

The LLM owns exactly one thing: which EXECUTE capabilities (training/life/walk/place)
and HANDOFF targets (skin/gait) the query semantically requests. Payload text,
coordinates, CLARIFY, handoff reasons, and the final RoutePlan are assembled
deterministically in planner.py.

v7 (D-051) adds the `place` destination. Place was already an executable capability
with a payload type, adapter, and bounded projection (PR #196), but it could only be
reached by the explicit `requested_capability=place` signal — so "오늘 산책하기 좋은
곳이 어디야?" ran Walk alone and answered with walking conditions instead of places.
v7 adds one destination and three boundary sentences: Place answers "where should I
go" (Walk answers "is now a good time"), a place noun that is only the *setting* of a
Training/Walk/Gait request is not a Place target, and both are selected when one
utterance asks both. A named area in the query never becomes a coordinate — Place
always searches around the trusted device location and says so (D-051 Option B;
named-region geocoding and a named-region CLARIFY are deferred to a separate card).

v8 (#279) adds ONE sentence and changes nothing else: an explicit natural-language
exclusion ("this, not that" / "only this") removes the excluded destination even when
its vocabulary is present. v7 already said negation overrides incidental vocabulary,
but the frozen `boundary_05`-shaped exclusion was still gaining Walk in every run
(#272: 3/3) — the sentence names the pattern. The same rule is mirrored in the agent
system prompt in the same PR (D-055 ⑦ rule 1). The general-answer fallback of #279 is
deliberately NOT a destination here: the router keeps selecting specialized
capabilities only, and the planner adds `general` by rule when nothing was selected.
The v8 regression against the same 80 gold cases is runner_v9.py.

The prompt below is `semantic-router-ko-v8`, which keeps intact everything of v7:
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
runner_v6.py (FAIL, one gate), v6 is runner_v7.py, and v7 is runner_v8.py.
The v7 Place acceptance set is evals/orchestration_router/gold_place_v1.jsonl.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from daengs_backend.config import settings

PROMPT_VERSION = "semantic-router-ko-v8"
ROUTER_MODEL_ID = "gemini-3.1-flash-lite"

# 생성 설정. 값은 D-041 이후 한 번도 바뀌지 않았고, 이름을 붙인 이유는 **에이전트 구현이
# 같은 값을 여기서 import 해 가기 위해서**다 (#272). 두 구현이 숫자를 따로 적으면 "같은
# 설정으로 쟀다" 를 코드가 증명하지 못한다 — v1 비교에서 에이전트는 temperature 를 명시하지
# 않아 프로바이더 기본값으로 돌았다.
ROUTER_TEMPERATURE = 0.0
ROUTER_CANDIDATE_COUNT = 1
ROUTER_MAX_OUTPUT_TOKENS = 256

# The only routing metadata the model may see. Coordinates deliberately stay out:
# Walk and Place payloads are built from trusted structured context, never from
# model output — including when the query names an area (D-051, Option B).
_ROUTING_METADATA_KEYS = ("source", "action", "active_dog_id")

ExecuteName = Literal["training", "life", "walk", "place"]
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
- execute.place: finding somewhere to go near the user — a kind of venue, a purpose, or a
  described place. Place answers "where should I go", not "is now a good time".
- handoffs.skin: inspecting a visible skin condition through the dedicated image flow.
- handoffs.gait: analyzing walking, limping, asymmetry, stride, posture, joint angles, or gait from
  an image/video through the dedicated gait flow. These descriptions do not make it an unsupported
  medical request.

Preserve multi-intent. Natural-language negation overrides incidental vocabulary. When the user
explicitly excludes a topic — asking for one thing and not another, or for one thing only — do not
select the excluded destination even though its vocabulary appears in the utterance. Skin and gait are
handoffs only. Select Walk only for current environmental walking suitability, such as weather,
heat, cold, rain, air quality, or similar environmental conditions; do not select Walk merely
because walking is the setting of a Training or Gait request. Route by meaning, not keyword
occurrence. Do not invent names.

Walk and Place answer different questions and are selected independently. Select Place when the
user asks where to go, which venue to visit, or to find or recommend a place. Do not select Place
merely because a place noun is the setting or backdrop of a Training, Walk, or Gait request —
asking whether today suits walking in a park is Walk alone, and asking how to train in a park is
Training alone. Select BOTH Place and Walk when one utterance asks both where to go and whether
current conditions suit walking. A named area, neighborhood, city, or landmark in the query does
not change which destinations are selected and is never a location value; select destinations as
usual and never emit, resolve, or imply coordinates for it.

General pet husbandry or care recommendations are NOT supported by any destination in v1: routine
or normative advice on how often or how long a dog should walk or exercise in general (per day, for
a breed, for an age or body size) independent of current conditions, feeding frequency or amount,
sleep duration, water intake, general grooming or care norms, and breed-, age-, or body-size-specific
care. Such a request is not Life even when it mentions an institution, an official source, or a
recommendation, is not Training unless it asks to change behavior or teach a skill, and is not
Place unless the user asks where to go — how often to bathe a dog is unsupported, while finding a
grooming shop is Place. By contrast,
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


def routing_metadata(context: dict[str, Any]) -> dict[str, str]:
    """The only context values any selector may show the model (Card 2A).

    Approved keys only, each a non-empty string; malformed internal context (a dict/list/
    blank under an approved key) must never reach a prompt. Shared by the semantic router
    and the agent selector (#272) so the two implementations see the same input surface —
    and, deliberately, so neither sees coordinates (D-051).
    """
    metadata: dict[str, str] = {}
    for key in _ROUTING_METADATA_KEYS:
        if key not in context:
            continue
        value = context[key]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"routing metadata {key} must be a non-empty string")
        metadata[key] = value
    return metadata


def build_semantic_router_prompt(*, query: str, context: dict[str, Any]) -> str:
    if not query.strip():
        raise ValueError("query must not be blank")
    metadata = routing_metadata(context)
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


def router_generation_config() -> Any:
    """The one generation config of the semantic router.

    A function rather than a constant only because `google.genai.types` stays a lazy
    import. The comparison runner's metered transport uses this same object, so the
    benchmark cannot drift from production by re-typing the numbers (#272).
    """
    from google.genai import types

    return types.GenerateContentConfig(
        temperature=ROUTER_TEMPERATURE,
        candidate_count=ROUTER_CANDIDATE_COUNT,
        max_output_tokens=ROUTER_MAX_OUTPUT_TOKENS,
        response_mime_type="application/json",
        response_json_schema=SemanticRoutingDecision.model_json_schema(),
    )


async def _generate_with_gemini(prompt: str) -> object:
    def _call() -> object:
        response = _gemini_client().models.generate_content(
            model=ROUTER_MODEL_ID,
            contents=prompt,
            config=router_generation_config(),
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
    "ROUTER_CANDIDATE_COUNT",
    "ROUTER_MAX_OUTPUT_TOKENS",
    "ROUTER_MODEL_ID",
    "ROUTER_TEMPERATURE",
    "GeminiSemanticRouter",
    "SemanticRoutingDecision",
    "SemanticRoutingError",
    "SocialIntent",
    "build_semantic_router_prompt",
    "router_generation_config",
    "routing_metadata",
    "validate_semantic_decision",
]
