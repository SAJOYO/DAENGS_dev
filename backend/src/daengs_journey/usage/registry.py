from functools import lru_cache

from daengs_journey.core.config import settings
from daengs_journey.usage.gate import UsageGate
from daengs_journey.usage.ledger import InMemoryLedger
from daengs_journey.usage.policy import BoundedDevPolicy, DenyAllPolicy


@lru_cache
def usage_gate() -> UsageGate:
    policy = BoundedDevPolicy() if settings.usage_policy == "dev" else DenyAllPolicy()
    return UsageGate(policy, InMemoryLedger())
