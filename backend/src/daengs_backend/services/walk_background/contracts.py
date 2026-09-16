"""Background outcomes and their existing canonical payload hash; no I/O or routing."""

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Collected:
    status: str
    reason: str | None = None
    payload: dict | None = None
    retrieved_at: str | None = None
    retryable: bool = False
    provider: str | None = None
    operation: str | None = None
    temporal_basis: str | None = None


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
