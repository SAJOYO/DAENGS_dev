"""DAENGS v1 orchestration contracts, route planning, and RoutePlan execution core.

Which implementation actually answers `/assistant/query` is `runtime.py`'s call —
see its module docstring for what the two implementations share and what they don't.
"""

from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    AssistantStatus,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    RoutePlan,
)
from daengs_backend.orchestration.graph import OrchestrationEngine
from daengs_backend.orchestration.runtime import Orchestrator, build_orchestrator
from daengs_backend.orchestration.service import AssistantOrchestrationService

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
