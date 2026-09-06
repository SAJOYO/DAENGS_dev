"""LangChain tool-calling agent orchestrator — the second implementation behind
`/assistant/query` (`runtime.py` picks between this and LangGraph).

Import this package only from `runtime.py`'s `agent` branch: LangChain lives in the
`agent` extra and is **absent from the default install** (server backend container
included), so a module-level import anywhere else stops the app from booting.
"""

from daengs_backend.orchestration.agent.service import (
    AGENT_MODEL_ID,
    AGENT_PROMPT_VERSION,
    AgentOrchestrationService,
)

__all__ = ["AGENT_MODEL_ID", "AGENT_PROMPT_VERSION", "AgentOrchestrationService"]
