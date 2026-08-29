from functools import lru_cache

from app.core.config import settings
from app.usage.gate import UsageGate
from app.usage.ledger import InMemoryLedger
from app.usage.policy import BoundedDevPolicy, DenyAllPolicy


@lru_cache
def usage_gate() -> UsageGate:
    policy = BoundedDevPolicy() if settings.usage_policy == "dev" else DenyAllPolicy()
    return UsageGate(policy, InMemoryLedger())
