from datetime import datetime

from daengs_journey.journey.contract import Companion, JourneyPlan, WalkPlan
from daengs_journey.journey.state import EditableState
from daengs_journey.providers.base import Mode


def resolve_journey(
    state: EditableState,
    *,
    now: datetime,
    companion: Companion,
    measured: bool,
) -> JourneyPlan:
    """Profile-free Journey portion of DAENGS_geo resolve_request at c5f0d5f."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must include a timezone")

    departure_at = now
    if state.time_intent is not None and state.time_intent.kind == "depart_at":
        departure_at = state.time_intent.at

    modes: list[Mode] = ["walk", "car", "transit"]
    preferred = state.journey.preferred_mode
    if preferred in modes:
        modes.remove(preferred)
        modes.insert(0, preferred)
    if state.urgency == "urgent":
        modes.remove("car")
        modes.insert(0, "car")

    return JourneyPlan(
        origin_lat=state.lat,
        origin_lng=state.lng,
        resolved_at=now,
        departure_at=departure_at,
        companion=companion,
        measured=measured,
        mode_priority=tuple(modes),
        max_total_min=state.journey.max_total_min,
        hard_limit=state.journey.hard_limit,
        walk=WalkPlan(max_walk_min=state.journey.walk.max_walk_min),
    )
