"""Consume existing verified retrace claims, never infer return from labels."""

from .contracts import slot


def evaluate(frame, previous):
    if previous is None:
        return slot("not_applicable", "No preceding record interval")
    observations = frame.get("relation_observations")
    if observations is None:
        return slot("insufficient_evidence", "No comparable verified movement interval")
    items = [o for o in observations if o["code"] == "retrace"]
    return slot(
        "confirmed" if items else "no_change",
        "Verified retrace claims in record interval; general route reconnection is not implemented",
        items,
    )
