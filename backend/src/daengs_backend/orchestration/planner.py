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

from daengs_backend.orchestration.contracts import (
    CapabilityName,
    CapabilityRequest,
    PlacePayload,
    RoutePlan,
    RouterKind,
)
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
    if requested_capability == CapabilityName.PLACE:
        missing = _missing_walk_coordinates(context)
        if missing:
            return RoutePlan.model_validate(
                {
                    "requests": [],
                    "handoffs": [],
                    "clarify": {
                        "question": _place_clarify_question(missing),
                        "missing": missing,
                    },
                    "router": RouterKind.DETERMINISTIC,
                    "model": None,
                }
            )
        location = context["location"]
        return RoutePlan(
            requests=[
                CapabilityRequest(
                    capability=CapabilityName.PLACE,
                    payload=PlacePayload(
                        query=query,
                        lat=location["lat"],
                        lon=location["lon"],
                    ),
                )
            ],
            router=RouterKind.DETERMINISTIC,
            model=None,
        )
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
            if capability == "life":
                dog = _dog_context(context)
                if dog is not None:
                    payload["dog"] = dog
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


def _dog_context(context: dict[str, Any]) -> dict[str, Any] | None:
    """Read the trusted dog facts, dropping anything the caller did not resolve.

    Same rule as ``context.location``: only the caller's structured values reach a payload,
    never model output. A malformed or empty entry yields None rather than an error, because
    a missing profile must not turn an answerable question into a failed request — Life
    answers without it exactly as it did before B4.
    """
    dog = context.get("dog")
    if not isinstance(dog, Mapping):
        return None
    resolved: dict[str, Any] = {}
    breed = dog.get("breed")
    if isinstance(breed, str) and breed.strip():
        resolved["breed"] = breed
    age_months = dog.get("age_months")
    if isinstance(age_months, int) and not isinstance(age_months, bool) and age_months >= 0:
        resolved["age_months"] = age_months
    return resolved or None


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


def _place_clarify_question(missing: list[str]) -> str:
    if len(missing) == 2:
        return "장소를 찾을 위치의 위도와 경도를 알려주세요."
    if missing == ["location.lat"]:
        return "장소를 찾을 위치의 위도를 알려주세요."
    return "장소를 찾을 위치의 경도를 알려주세요."


__all__ = ["assemble_route_plan", "resolve_deterministic_route"]
