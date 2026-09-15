"""Relational execution policy and result, independent of the old card writer contract."""

from dataclasses import dataclass, replace
from math import isfinite

MODEL = "gemini-3.1-flash-lite"


class RelationalConfigurationError(RuntimeError):
    """Configured provider cannot start; no retry/pacing loop should run."""


@dataclass(frozen=True)
class RelationalExecutionPolicy:
    preparation_timeout_s: float = 12.0
    generation_timeout_s: float | None = None
    call_timeout_s: float = 15.0
    minimum_interval_s: float = 10.0
    max_calls: int = 64
    semantic_review: bool = True

    def __post_init__(self):
        for value in (self.preparation_timeout_s, self.call_timeout_s):
            if not isfinite(value) or value <= 0:
                raise ValueError("execution timeouts must be finite and positive")
        if self.generation_timeout_s is not None and (
            not isfinite(self.generation_timeout_s) or self.generation_timeout_s <= 0
        ):
            raise ValueError("invalid generation timeout")
        if not isfinite(self.minimum_interval_s) or self.minimum_interval_s < 0:
            raise ValueError("invalid call interval")
        if type(self.max_calls) is not int or self.max_calls < 0:
            raise ValueError("invalid call budget")
        if type(self.semantic_review) is not bool:
            raise TypeError("semantic review must be explicit")

    def call_budget(self, scene_count, action_count):
        if (
            any(type(n) is not int or n < 0 for n in (scene_count, action_count))
            or action_count > scene_count
        ):
            raise ValueError("invalid scene/action counts")
        # Any space introduction may need recovery; one title follows the bodies.
        return min(
            self.max_calls, (scene_count + action_count + 1) * (2 if self.semantic_review else 1)
        )

    def resolve(self, scene_count, action_count):
        calls = self.call_budget(scene_count, action_count)
        if self.generation_timeout_s is not None:
            return self  # An explicit operator/test deadline is never silently extended.
        seconds = max(
            180.0, calls * self.call_timeout_s + max(0, calls - 1) * self.minimum_interval_s + 15.0
        )
        return replace(self, generation_timeout_s=seconds)


@dataclass(frozen=True)
class RelationalDiaryResult:
    """Prepared facts and the frozen v7 receipt; not a CardWritingResult or DB publication."""

    input_revision: str
    board_revision: str
    prepared: dict
    receipt: dict
