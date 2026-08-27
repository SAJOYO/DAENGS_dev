"""standalone 정책 조립. 팀 서비스는 이 조립점만 자기 Policy/Ledger로 교체한다."""

import logging
from functools import lru_cache

from app.core.config import settings
from app.usage.gate import UsageGate
from app.usage.ledger import InMemoryLedger
from app.usage.policy import BoundedDevPolicy, DenyAllPolicy

logger = logging.getLogger(__name__)


@lru_cache
def usage_gate() -> UsageGate:
    if settings.usage_policy == "dev":
        logger.warning(
            "bounded development usage policy enabled; limits are process-local and reset on restart"
        )
        policy = BoundedDevPolicy()
    else:
        policy = DenyAllPolicy()
    return UsageGate(policy, InMemoryLedger())
