"""Shared snapshot values for photo, background and diary contracts.

These preserve the v1 JSON/hash representation; they do not replace measurement
coordinates or GPS fingerprints, which have independent precision contracts.
"""

import hashlib
import json
from datetime import UTC
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, ConfigDict, Field

from daengs_walk.contracts import FrozenContract

Instant = Annotated[AwareDatetime, AfterValidator(lambda at: at.astimezone(UTC))]


class ValueContract(FrozenContract):
    # Preserve user text verbatim, and reject non-finite numbers.
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Point(ValueContract):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


def digest(value) -> str:
    if isinstance(value, ValueContract):
        value = value.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
