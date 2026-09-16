"""Every relation family has a visible result, including unavailable families."""

from typing import Literal

from daengs_walk.value_contracts import ValueContract


class RelationSlot(ValueContract):
    status: Literal[
        "confirmed",
        "no_change",
        "insufficient_evidence",
        "not_applicable",
        "not_implemented",
        "failed",
    ]
    policy_version: str = "relation-slots-v1"
    reason: str
    items: tuple[dict, ...] = ()


class RelationSlots(ValueContract):
    background: RelationSlot
    proximity: RelationSlot
    continuity: RelationSlot
    route_revisit: RelationSlot
    movement: RelationSlot
    event_context: RelationSlot


def slot(status, reason, items=()):
    return RelationSlot(status=status, reason=reason, items=items).model_dump(mode="json")
