from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.journey.planning import resolve_journey
from app.journey.state import CURRENT_STATE_VERSION, EditableState


def test_legacy_state_migrates_removed_axes_without_losing_journey_values() -> None:
    state = EditableState.model_validate(
        {
            "state_version": 3,
            "lat": 37.5,
            "lng": 127.0,
            "target": {
                "night": True,
                "emergency": True,
                "at": "2026-08-21T12:00:00Z",
                "specialty": ["ortho"],
            },
            "journey": {
                "walk": {
                    "option": "no_stairs",
                    "avoid": ["stairs"],
                    "max_walk_min": 10,
                }
            },
        }
    )

    assert state.state_version == CURRENT_STATE_VERSION
    assert state.target.night_service is True
    assert state.target.emergency_service is True
    assert state.time_intent is not None
    assert state.time_intent.kind == "service_at"
    assert state.journey.walk.max_walk_min == 10


def test_unknown_state_fields_and_versions_still_fail() -> None:
    with pytest.raises(ValidationError):
        EditableState.model_validate({"lat": 37.5, "lng": 127.0, "typo": True})
    with pytest.raises(ValidationError):
        EditableState.model_validate(
            {"state_version": 999, "lat": 37.5, "lng": 127.0}
        )


def test_departure_time_and_preferred_mode_flow_to_the_journey_plan() -> None:
    departure = datetime(2026, 8, 21, 13, 0, tzinfo=UTC)
    now = datetime(2026, 8, 21, 3, 0, tzinfo=UTC)
    state = EditableState.model_validate(
        {
            "lat": 37.5,
            "lng": 127.0,
            "time_intent": {"kind": "depart_at", "at": departure.isoformat()},
            "journey": {"preferred_mode": "transit"},
        }
    )

    plan = resolve_journey(state, now=now, companion="dog", measured=False)

    assert plan.departure_at == departure
    assert plan.mode_priority[0] == "transit"
