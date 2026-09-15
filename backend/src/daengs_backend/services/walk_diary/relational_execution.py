"""Relational execution policy and result, independent of the old card writer contract."""

from dataclasses import dataclass
from math import isfinite

MODEL = "gemini-3.1-flash-lite"


@dataclass(frozen=True)
class RelationalExecutionPolicy:
    preparation_timeout_s: float = 12.0
    generation_timeout_s: float = 180.0
    call_timeout_s: float = 15.0
    minimum_interval_s: float = 10.0
    max_calls: int = 64
    semantic_review: bool = True

    def __post_init__(self):
        for value in (self.preparation_timeout_s, self.generation_timeout_s, self.call_timeout_s):
            if not isfinite(value) or value <= 0:
                raise ValueError("execution timeouts must be finite and positive")
        if not isfinite(self.minimum_interval_s) or self.minimum_interval_s < 0:
            raise ValueError("invalid call interval")
        if type(self.max_calls) is not int or self.max_calls < 0:
            raise ValueError("invalid call budget")
        if type(self.semantic_review) is not bool:
            raise TypeError("semantic review must be explicit")


@dataclass(frozen=True)
class RelationalDiaryResult:
    """Prepared facts and the frozen v7 receipt; not a CardWritingResult or DB publication."""

    input_revision: str
    board_revision: str
    prepared: dict
    receipt: dict
