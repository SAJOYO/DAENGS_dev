"""DAENGS v1 orchestration contracts, route planning, and RoutePlan execution core.

Which implementation actually answers `/assistant/query` is `runtime.py`'s call —
see its module docstring for what the two implementations share and what they don't.
"""

from importlib import import_module

from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    AssistantStatus,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    RoutePlan,
)

_LAZY_EXPORTS = {
    "OrchestrationEngine": ".graph",
    "Orchestrator": ".runtime",
    "build_orchestrator": ".runtime",
    "AssistantOrchestrationService": ".service",
}


def __getattr__(name):
    # Shared contracts/execution must not import assistant graphs or providers.
    # Preserve existing public imports, including frozen benchmark callers.
    module = _LAZY_EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module, __name__), name)
    globals()[name] = value
    return value


__all__ = [
    "AssistantOrchestrationService",
    "AssistantResponse",
    "AssistantStatus",
    "CapabilityRequest",
    "CapabilityResult",
    "CapabilityStatus",
    "OrchestrationEngine",
    "Orchestrator",
    "RoutePlan",
    "build_orchestrator",
]
