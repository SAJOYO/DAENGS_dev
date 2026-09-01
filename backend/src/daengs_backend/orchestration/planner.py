"""Deterministic route planning around the semantic decision (D-041, Card 2B).

Two responsibilities, both deterministic:

1. Resolve the approved machine-readable routing signal (`requested_capability`,
   routing doc §1) before any LLM call. It is a routing signal, never
   authorization (D-036), and no new deterministic signals are invented here.
2. Assemble the real Card 1 RoutePlan from a SemanticRoutingDecision using only
   the trusted query/context: Training/Life carry the exact original query, Walk
   coordinates come only from context.location, handoff reasons are fixed, and
   missing Walk coordinates produce an exclusive CLARIFY.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from daengs_backend.orchestration.contracts import RoutePlan, RouterKind
from daengs_backend.orchestration.semantic import ROUTER_MODEL_ID, SemanticRoutingDecision

_EXECUTE_NAMES = frozenset({"training", "life", "walk"})
_HANDOFF_REASONS = {
    "skin": "image_upload_required",
    "gait": "video_upload_required",
}
_COORDINATE_BOUNDS = (("lat", 33.0, 39.0), ("lon", 124.0, 132.0))


def resolve_deterministic_route(
    *, requested_capability: str | None, query: str, context: dict[str, Any]
) -> RoutePlan | None:
    """Return a deterministic RoutePlan when the approved signal resolves it, else None."""
    if requested_capability is None:
        return None
    if requested_capability in _EXECUTE_NAMES:
        decision = SemanticRoutingDecision(execute=[requested_capability], handoffs=[])
    elif requested_capability in _HANDOFF_REASONS:
        decision = SemanticRoutingDecision(execute=[], handoffs=[requested_capability])
    else:
        # An unresolved signal does not fail the request; semantic routing decides.
        return None
    return assemble_route_plan(
        decision, query=query, context=context, router=RouterKind.DETERMINISTIC, model=None
    )


def assemble_route_plan(
    decision: SemanticRoutingDecision,
    *,
    query: str,
    context: dict[str, Any],
    router: RouterKind,
    model: str | None = ROUTER_MODEL_ID,
) -> RoutePlan:
    """Build the real Card 1 RoutePlan using only trusted query/context values."""
    missing = _missing_walk_coordinates(context) if "walk" in decision.execute else []
    if missing:
        # CLARIFY is exclusive (O-8): nothing executes and nothing hands off first.
        return RoutePlan.model_validate(
            {
                "requests": [],
                "handoffs": [],
                "clarify": {"question": _clarify_question(missing), "missing": missing},
                "router": router,
                "model": model,
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

    return RoutePlan.model_validate(
        {
            "requests": requests,
            "handoffs": [
                {"target": target, "reason": _HANDOFF_REASONS[target]}
                for target in decision.handoffs
            ],
            "clarify": None,
            "router": router,
            "model": model,
        }
    )


def _missing_walk_coordinates(context: dict[str, Any]) -> list[str]:
    location = context.get("location")
    if not isinstance(location, Mapping):
        return ["location.lat", "location.lon"]
    missing = []
    for key, low, high in _COORDINATE_BOUNDS:
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


__all__ = ["assemble_route_plan", "resolve_deterministic_route"]
