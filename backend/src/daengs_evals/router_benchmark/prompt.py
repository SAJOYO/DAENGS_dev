"""Frozen semantic-router prompt builder; benchmark truth is never an input."""

from __future__ import annotations

import json
from typing import Any

from daengs_backend.orchestration.contracts import RoutePlan

PROMPT_VERSION = "semantic-router-ko-v1"
MODEL_ID = "gemini-3.5-flash-lite"

_ALLOWED_CONTEXT_KEYS = frozenset({"location", "source", "action", "active_dog_id"})
_FORBIDDEN_KEYS = frozenset(
    {
        "authorization",
        "jwt",
        "jwe",
        "access_token",
        "refresh_token",
        "cookie",
        "category",
        "gold_route_plan",
        "rationale",
        "expected_capability",
        "metric",
        "metrics",
    }
)

_POLICY = """You are the DAENGS semantic router, not an answer generator.

Return exactly one JSON object conforming to the supplied RoutePlan schema. Return no Markdown,
code fence, prose, explanation, diagnosis, advice, or tool result.

Routing policy:
1. EXECUTE is represented only by requests[]. Allowed executable capabilities are training, life,
   and walk. Preserve every independently requested capability; requests[] and handoffs[] may coexist.
2. training owns requests about changing dog behavior or teaching skills. life owns evidence-backed
   informational questions about rules, institutions, procedures, conditions, fees, deadlines,
   official guidance, insurance terms, or pet travel rules. walk owns current environmental walking
   suitability and requires valid location.lat and location.lon from structured context.
3. HANDOFF is represented only by handoffs[]. The only targets are skin and gait. Skin text requests
   go to the image-upload flow with reason image_upload_required. Gait/video-analysis requests go to
   the video-upload flow with reason video_upload_required. Never put skin or gait in requests[].
4. CLARIFY is only for genuinely missing information required to construct a valid plan. If clarify
   is not null, requests and handoffs must both be empty. Missing Walk coordinates use the canonical
   keys location.lat and location.lon. A schema failure, uncertainty, short wording, or downstream
   capability abstention is not CLARIFY. Do not partially execute or hand off when clarification is
   required; the next request is stateless and will include the original query plus new context.
5. Route by meaning, not keyword occurrence. A capability word may be incidental or explicitly
   negated. Do not collapse multi-intent input into one label. Do not invent capability or handoff
   names. Medical, emergency, place, and journey are unresolved/out of scope for this scored v1
   taxonomy; do not map them to an allowed target. A resolved but unsupported request gets an empty
   plan, not a fabricated route and not CLARIFY.
6. Copy the full user query into TrainingPayload.question or LifePayload.question. For WalkPayload,
   copy numeric coordinates from context.location. Do not invent coordinates. Do not add duplicates
   or timeout overrides.
7. Set router to \"llm\" and model to \"gemini-3.5-flash-lite\".

Critical prohibitions: do not answer the dog question; do not give training or health advice; do not
diagnose; do not execute tools; do not use keyword rules; do not expose explanations outside the
RoutePlan; do not return Markdown."""


def build_router_prompt(*, query: str, context: dict[str, Any]) -> str:
    """Build one routing prompt from public policy, query, context and the real schema only.

    The keyword-only signature intentionally has no category, rationale or gold-plan parameter.
    """
    if not query.strip():
        raise ValueError("query must not be blank")
    unknown = set(context) - _ALLOWED_CONTEXT_KEYS
    if unknown:
        raise ValueError(f"unsupported router context keys: {sorted(unknown)}")
    _reject_forbidden_keys(context)
    _validate_context_shape(context)
    schema = json.dumps(RoutePlan.model_json_schema(), ensure_ascii=False, sort_keys=True)
    serialized_context = json.dumps(
        context, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return (
        f"PROMPT_VERSION: {PROMPT_VERSION}\n\n"
        f"{_POLICY}\n\n"
        f"ROUTE_PLAN_JSON_SCHEMA:\n{schema}\n\n"
        f"INPUT_LOCALE: ko-KR\n"
        f"STRUCTURED_CONTEXT: {serialized_context}\n"
        f"USER_QUERY: {query}\n"
    )


def _reject_forbidden_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in _FORBIDDEN_KEYS:
                raise ValueError(f"forbidden router context key: {key}")
            _reject_forbidden_keys(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_forbidden_keys(item)


def _validate_context_shape(context: dict[str, Any]) -> None:
    location = context.get("location")
    if location is not None:
        if not isinstance(location, dict) or set(location) - {"lat", "lon"}:
            raise ValueError("location may contain only lat and lon")
        for key, low, high in (("lat", 33.0, 39.0), ("lon", 124.0, 132.0)):
            if key not in location:
                continue
            value = location[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"location.{key} must be numeric")
            if not low <= float(value) <= high:
                raise ValueError(f"location.{key} is outside the v1 service area")
    for key in ("source", "action", "active_dog_id"):
        if key in context and (not isinstance(context[key], str) or not context[key].strip()):
            raise ValueError(f"{key} must be a non-empty string")
