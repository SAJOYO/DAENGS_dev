from dataclasses import dataclass
from typing import ClassVar, Literal

from daengs_journey.providers.base import Mode


@dataclass(frozen=True)
class MeasuredRouteIntent:
    mode: Mode
    operation: ClassVar[str] = "route.measure"
    units: ClassVar[int] = 1


@dataclass(frozen=True)
class UsageWindow:
    bucket: str
    max_units: int
    seconds: int


@dataclass(frozen=True)
class UsagePermit:
    allowed: bool
    reason: str
    max_units_per_request: int | None = None
    window: UsageWindow | None = None


type DenialCode = Literal[
    "policy_denied", "request_limit", "usage_limit", "request_scope_missing"
]


class UsageDenied(RuntimeError):
    def __init__(self, code: DenialCode, reason: str, *, retry_after_s: int | None = None):
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.retry_after_s = retry_after_s
