"""Semantic-only v2 prompt; trusted RoutePlan data is assembled outside Gemini."""

from __future__ import annotations

import json
from typing import Any

from .prompt import MODEL_ID
from .semantic_v2 import SemanticRoutingDecision

PROMPT_VERSION = "semantic-router-ko-v2"

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
handoffs only. Route by meaning, not keyword occurrence. Do not invent names. If no supported
destination is semantically requested, return both lists empty."""


def build_semantic_router_prompt(*, query: str, context: dict[str, Any]) -> str:
    if not query.strip():
        raise ValueError("query must not be blank")
    metadata = {
        key: context[key] for key in ("source", "action", "active_dog_id") if key in context
    }
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


__all__ = ["MODEL_ID", "PROMPT_VERSION", "build_semantic_router_prompt"]
