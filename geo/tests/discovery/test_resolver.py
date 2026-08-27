import inspect
from datetime import UTC, datetime

from app.discovery.facts import RuntimeFacts
from app.discovery.resolver import resolve_request
from app.discovery.semantics import TimeIntent, UrgencySignal
from app.discovery.state import EditableState, JourneyPrefs
from app.features.journey.api import Dest, JourneyIn, journey
from app.geo.search import find_places
from app.journey.engine import snapshot
from app.profile.source import OWNERS, PERSONAS
from app.providers.base import LatLng

NOW = datetime(2026, 8, 21, 3, 0, tzinfo=UTC)          # 한국 낮 12시
NIGHT = datetime(2026, 8, 21, 13, 0, tzinfo=UTC)       # 한국 밤 10시
RULE_URGENT = UrgencySignal(value="urgent", origin="rule", reason="breathing_rule")


def _resolve(state: EditableState, *, signals=(), dog="halmae", measured=False):
    return resolve_request(
        state,
        RuntimeFacts(
            now=NOW,
            profile=PERSONAS[dog],
            owner=OWNERS["myeongsu"],
            temp_c=31,
            urgency_signals=tuple(signals),
        ),
        kind="hospital",
        companion="dog",
        measured=measured,
    )


def test_unspecified_urgency_does_not_suppress_a_runtime_rule():
    state = EditableState(lat=37.5, lng=127.0)
    assert state.urgency is None

    resolved = _resolve(state, signals=(RULE_URGENT,))

    assert resolved.journey.mode_priority[0] == "car"
    assert resolved.search.must.open_now is True
    assert {"emergency", "24h"} <= set(resolved.search.prefer.tags)
    assert resolved.view.sort == "duration"
    assert resolved.view.show_call_cta is True


def test_explicit_calm_controls_planning_but_not_the_safety_surface():
    state = EditableState(lat=37.5, lng=127.0, urgency="normal")
    state.journey.preferred_mode = "walk"

    resolved = _resolve(state, signals=(RULE_URGENT,))

    assert resolved.journey.mode_priority[0] == "walk"
    assert resolved.search.must.open_now is False
    assert resolved.view.sort == "distance"
    assert resolved.view.show_call_cta is True
    assert resolved.view.call_reasons == ("breathing_rule",)


def test_service_time_and_departure_time_flow_to_different_plans():
    service = EditableState(lat=37.5, lng=127.0,
                            time_intent=TimeIntent(kind="service_at", at=NIGHT))
    service_resolved = _resolve(service)
    assert service_resolved.search.must.judge_at == NIGHT
    assert service_resolved.journey.departure_at == NOW

    departure = EditableState(lat=37.5, lng=127.0,
                              time_intent=TimeIntent(kind="depart_at", at=NIGHT))
    departure_resolved = _resolve(departure)
    assert departure_resolved.search.must.judge_at == NOW
    assert departure_resolved.journey.departure_at == NIGHT


def test_owner_and_profile_decide_transit_availability_in_the_resolver():
    state = EditableState(lat=37.5, lng=127.0)
    state.journey.preferred_mode = "transit"
    resolved = _resolve(state, dog="halmae")
    assert "transit" not in resolved.journey.mode_priority  # owner transit_ok=False
    assert resolved.journey.temp_c == 31
    assert any(e.overrode == "journey.preferred_mode" for e in resolved.trace.entries)


def test_duration_sort_is_not_claimed_when_search_skips_transport():
    state = EditableState(lat=37.5, lng=127.0, urgency="urgent")
    resolved = resolve_request(
        state,
        RuntimeFacts(now=NOW),
        kind="hospital",
        companion="dog",
        measured=False,
        transport_available=False,
    )
    assert resolved.view.sort == "distance"
    assert any("transport unavailable" in entry.because for entry in resolved.trace.entries)


async def test_urgent_trip_keeps_constraints_and_prefers_a_faster_mode():
    state = EditableState(lat=37.5, lng=127.0, urgency="urgent")
    state.journey.walk.max_walk_min = 1
    plan = _resolve(state).journey

    transport = await snapshot(plan, LatLng(37.52, 127.02))

    assert transport.as_of == NOW
    assert transport.mode_priority[0] == "car"
    assert transport.walk.advice == "avoid"
    assert any("제한 1분" in why for why in transport.walk.why)


def test_engines_no_longer_expose_the_old_kwargs_bypass():
    snapshot_params = set(inspect.signature(snapshot).parameters)
    search_params = set(inspect.signature(find_places).parameters)
    assert {"measured", "mode", "profile", "temp_c", "at"}.isdisjoint(snapshot_params)
    assert search_params == {"db", "plan", "only_dog_ok"}


async def test_journey_api_keeps_legacy_prefs_but_new_state_carries_context():
    legacy = await journey(
        JourneyIn(
            origin=(37.5, 127.0),
            dests=[Dest(lat=37.51, lng=127.01)],
            prefs=JourneyPrefs(preferred_mode="transit"),
            measured=False,
        ),
        None,  # 좌표 목적지는 DB를 쓰지 않는다
    )
    assert legacy.items[0].transport.mode_priority[0] == "transit"

    state = EditableState(lat=0, lng=0, urgency="urgent")
    state.journey.preferred_mode = "walk"
    current = await journey(
        JourneyIn(
            origin=(37.5, 127.0),
            dests=[Dest(lat=37.51, lng=127.01)],
            state=state,
            measured=False,
        ),
        None,
    )
    assert current.items[0].transport.mode_priority[0] == "car"
