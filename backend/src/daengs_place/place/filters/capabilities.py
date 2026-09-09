"""Filter capability metadata; legacy planning modes remain unchanged."""

from dataclasses import dataclass
from typing import Literal

Capability = Literal["purpose.kind", "operations.parking", "pet_access.exclusive"]


@dataclass(frozen=True)
class CapabilitySpec:
    id: Capability
    label: str
    value_type: Literal["kind_set", "boolean"]
    operators: tuple[str, ...]
    fact_path: str
    sql_column: str
    prefer_values: tuple[bool, ...] = ()


CAPABILITIES = (
    CapabilitySpec("purpose.kind", "업종", "kind_set", ("in", "not_in"), "match.kind", "kind"),
    CapabilitySpec(
        "operations.parking",
        "주차 여부",
        "boolean",
        ("eq",),
        "facts.parking",
        "filter_parking",
        (True,),
    ),
    CapabilitySpec(
        "pet_access.exclusive",
        "반려동물 전용 여부",
        "boolean",
        ("eq",),
        "facts.pet_access.exclusive",
        "filter_exclusive",
    ),
)
BY_ID = {spec.id: spec for spec in CAPABILITIES}
