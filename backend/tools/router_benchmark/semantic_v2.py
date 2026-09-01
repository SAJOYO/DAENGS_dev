"""Benchmark-only semantic decision and deterministic RoutePlan assembly for v2."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import Field, ValidationError, model_validator

from daengs_backend.orchestration.contracts import RoutePlan

from .schemas import StrictModel

ExecuteName = Literal["training", "life", "walk"]
HandoffName = Literal["skin", "gait"]
UniqueExecuteList = Annotated[list[ExecuteName], Field(json_schema_extra={"uniqueItems": True})]
UniqueHandoffList = Annotated[list[HandoffName], Field(json_schema_extra={"uniqueItems": True})]


class SemanticRoutingDecision(StrictModel):
    """The complete and deliberately small output surface owned by Gemini."""

    execute: UniqueExecuteList = Field(default_factory=list)
    handoffs: UniqueHandoffList = Field(default_factory=list)

    @model_validator(mode="after")
    def capabilities_are_unique(self) -> SemanticRoutingDecision:
        if len(self.execute) != len(set(self.execute)):
            raise ValueError("execute capabilities must be unique")
        if len(self.handoffs) != len(set(self.handoffs)):
            raise ValueError("handoff targets must be unique")
        return self


class SemanticValidation(StrictModel):
    schema_valid: bool
    decision: SemanticRoutingDecision | None = None
    execute_names: list[str] = Field(default_factory=list)
    handoff_names: list[str] = Field(default_factory=list)
    error_category: str | None = None


def validate_semantic_decision(raw: object) -> SemanticValidation:
    """Validate provider output and retain only normalized invalid-output metadata."""
    parsed: object = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return SemanticValidation(schema_valid=False, error_category="invalid_json")
    execute_names = _raw_names(parsed, "execute")
    handoff_names = _raw_names(parsed, "handoffs")
    try:
        decision = (
            parsed
            if isinstance(parsed, SemanticRoutingDecision)
            else SemanticRoutingDecision.model_validate(parsed)
        )
    except (ValidationError, ValueError, TypeError):
        return SemanticValidation(
            schema_valid=False,
            execute_names=execute_names,
            handoff_names=handoff_names,
            error_category="schema_validation_error",
        )
    return SemanticValidation(
        schema_valid=True,
        decision=decision,
        execute_names=list(decision.execute),
        handoff_names=list(decision.handoffs),
    )


def assemble_route_plan(
    decision: SemanticRoutingDecision,
    *,
    query: str,
    context: dict[str, Any],
) -> RoutePlan:
    """Build the real Card 1 RoutePlan using only trusted query/context values."""
    missing = _missing_walk_coordinates(context) if "walk" in decision.execute else []
    if missing:
        return RoutePlan.model_validate(
            {
                "requests": [],
                "handoffs": [],
                "clarify": {"question": _clarify_question(missing), "missing": missing},
                "router": "llm",
                "model": "gemini-3.5-flash-lite",
            }
        )

    requests: list[dict[str, Any]] = []
    for capability in decision.execute:
        if capability in {"training", "life"}:
            payload: dict[str, Any] = {"question": query}
        else:
            location = context["location"]
            payload = {"lat": location["lat"], "lon": location["lon"]}
        requests.append({"capability": capability, "payload": payload, "timeout_ms": None})

    reason_by_target = {
        "skin": "image_upload_required",
        "gait": "video_upload_required",
    }
    return RoutePlan.model_validate(
        {
            "requests": requests,
            "handoffs": [
                {"target": target, "reason": reason_by_target[target]}
                for target in decision.handoffs
            ],
            "clarify": None,
            "router": "llm",
            "model": "gemini-3.5-flash-lite",
        }
    )


def _missing_walk_coordinates(context: dict[str, Any]) -> list[str]:
    location = context.get("location")
    if not isinstance(location, Mapping):
        return ["location.lat", "location.lon"]
    missing = []
    for key, low, high in (("lat", 33.0, 39.0), ("lon", 124.0, 132.0)):
        value = location.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not low <= value <= high
        ):
            missing.append(f"location.{key}")
    return missing


def _clarify_question(missing: list[str]) -> str:
    if len(missing) == 2:
        return "산책할 위치의 위도와 경도를 알려주세요."
    if missing == ["location.lat"]:
        return "현재 위치의 위도를 알려주세요."
    return "현재 위치의 경도를 알려주세요."


def _raw_names(raw: object, key: str) -> list[str]:
    if isinstance(raw, SemanticRoutingDecision):
        return list(getattr(raw, key))
    if not isinstance(raw, Mapping) or not isinstance(raw.get(key), list):
        return []
    return [str(item) for item in raw[key] if isinstance(item, str)]
