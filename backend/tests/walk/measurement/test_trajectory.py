import pytest

from daengs_walk.trajectory import EvidenceJournal, IntervalLedger, SourceRange
from daengs_walk.trajectory_selection import (
    EventTime,
    ReplaySelection,
    SpanTime,
    UnknownTime,
    WalkSelection,
    validate_time_address,
)
from tests.walk.measurement.trajectory_support import CASES, assessment, fixture, rebuild, snapshot


def test_bypass_replaces_owners_and_keeps_old_candidates_out_of_totals():
    case, refs, before = fixture()
    after = before.replace((assessment(case["replacement"], refs),))
    expected = case["expected"]
    assert before.metrics().walking_distance_m == expected["initial_distance_m"]
    assert after.metrics().walking_distance_m == expected["final_distance_m"]
    assert sum(i.walking_distance_m for i in after.superseded) == expected["superseded_distance_m"]
    assert after.metrics().known_duration_ns == before.metrics().known_duration_ns
    assert after.metrics().known_duration_ns == expected["known_duration_ns"]
    assert before.journal == after.journal
    assert len(after.intervals) == len(before.intervals) - 1


@pytest.mark.parametrize("invalid", ["overlap", "hole", "reverse", "missing_tail"])
def test_final_ownership_must_cover_each_source_edge_exactly_once(invalid):
    case, refs, ledger = fixture()
    intervals = list(ledger.intervals)
    if invalid == "overlap":
        intervals.insert(1, assessment(case["replacement"], refs))
    elif invalid == "hole":
        intervals.pop(2)
    elif invalid == "reverse":
        intervals[1:3] = reversed(intervals[1:3])
    else:
        intervals.pop()
    with pytest.raises(ValueError, match="partition|cover"):
        rebuild(ledger, intervals=intervals)


def test_partial_replacement_cannot_prorate_an_existing_owner():
    case, refs, ledger = fixture()
    after = ledger.replace((assessment(case["replacement"], refs),))
    with pytest.raises(ValueError, match="ownership boundaries"):
        after.replace((ledger.intervals[1],))


@pytest.mark.parametrize(
    "changes",
    [
        {"continuity": "broken"},
        {"walking_use": "excluded"},
        {"activity": "still"},
        {"walking_distance_m": float("nan")},
        {"walking_distance_m": float("inf")},
        {"walking_distance_m": -1},
    ],
)
def test_illegal_walking_contributions_fail_at_contract_boundary(changes):
    _, _, ledger = fixture()
    with pytest.raises(ValueError):
        rebuild(ledger.intervals[1], **changes)


def test_bypass_requires_its_own_evidence():
    case, refs, ledger = fixture()
    replacement = assessment(case["replacement"], refs)
    with pytest.raises(ValueError, match="reassessment"):
        ledger.replace((rebuild(replacement, reassessment_basis=None),))


def test_walk_reentry_requires_its_own_evidence():
    _, _, ledger = fixture("walking_excluded_gap_still_restart")
    intervals = list(ledger.intervals)
    intervals[5] = rebuild(intervals[5], reentry_basis=None)
    with pytest.raises(ValueError, match="reentry"):
        rebuild(ledger, intervals=intervals)


@pytest.mark.parametrize("control", ["pause", "loss"])
def test_reassessment_cannot_erase_a_control_boundary(control):
    case, refs, ledger = fixture()
    events = list(ledger.journal.events)
    events[2] = rebuild(events[2], kind=control)
    journal = EvidenceJournal(events=tuple(events))
    replacement = assessment(case["replacement"], refs)
    with pytest.raises(ValueError, match="control"):
        IntervalLedger(
            journal=journal,
            points=tuple(p for p in ledger.points if p.ref != refs["X"]),
            intervals=(ledger.intervals[0], replacement, *ledger.intervals[3:]),
        )


def test_zero_walking_distance_distinguishes_excluded_motion_gap_and_still():
    _, _, ledger = fixture("walking_excluded_gap_still_restart")
    metrics = ledger.metrics()
    assert ledger.intervals[2].measured_displacement_m == 120
    assert ledger.intervals[2].walking_distance_m == 0
    assert metrics.walking_distance_m == 20
    assert metrics.known_duration_ns == 63_000_000_000
    assert metrics.located_duration_ns == 40_000_000_000
    assert metrics.observed_still_duration_ns == 10_000_000_000
    assert metrics.unlocated_duration_ns == 23_000_000_000
    assert metrics.unresolved_walking_duration_ns == 20_000_000_000
    assert metrics.average_walking_speed_mps == 1
    assert metrics.located_fraction_of_known_time == pytest.approx(40 / 63)


def test_45_seconds_after_start_is_selectable_without_any_gps_point():
    _, refs, ledger = fixture("first_fix_after_three_minutes")
    cursor = SpanTime(
        source_range=SourceRange(start=refs["start"], end=refs["A"]), offset_ns=45_000_000_000
    )
    selection = WalkSelection(
        session_id="synthetic-walk", measurement_id="R1", target=ReplaySelection(cursor=cursor)
    )
    selection.validate_against(ledger.journal)
    assert selection.target.cursor.offset_ns == 45_000_000_000
    assert ledger.metrics().observed_still_duration_ns == 0
    validate_time_address(ledger.journal, EventTime(ref=refs["end"]))
    with pytest.raises(ValueError, match="in-range"):
        validate_time_address(ledger.journal, rebuild(cursor, offset_ns=181_000_000_000))


def test_unknown_epoch_has_an_address_but_no_invented_duration():
    _, refs, ledger = fixture("unknown_epoch_duration")
    span = SourceRange(start=refs["A"], end=refs["B"])
    validate_time_address(ledger.journal, UnknownTime(source_range=span))
    assert ledger.journal.duration_ns(span) is None
    assert ledger.metrics().unknown_duration_intervals == 1
    assert ledger.metrics().known_duration_ns == 21_000_000_000
    with pytest.raises(ValueError, match="known duration"):
        validate_time_address(ledger.journal, SpanTime(source_range=span, offset_ns=0))
    with pytest.raises(ValueError, match="not timed"):
        ReplaySelection(cursor=UnknownTime(source_range=span), playback="playing")


@pytest.mark.parametrize(
    "target",
    [
        {"mode": "overview", "event_id": "scene"},
        {"mode": "scene"},
        {"mode": "range", "source_ranges": []},
        {"mode": "replay", "event_id": "scene"},
    ],
)
def test_selection_modes_forbid_ambiguous_field_combinations(target):
    with pytest.raises(ValueError):
        WalkSelection(session_id="synthetic-walk", measurement_id="R1", target=target)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
@pytest.mark.parametrize("chunk_size", [1, 2, 3, 256])
def test_contract_payload_reassembly_preserves_source_order_and_final_result(case, chunk_size):
    # This tests transport partitioning, not incremental GPS classification.
    _, _, ledger = fixture(case["name"])
    events = ledger.journal.model_dump(mode="json")["events"]
    chunks = [events[i : i + chunk_size] for i in range(0, len(events), chunk_size)]
    assembled = EvidenceJournal.model_validate({"events": [e for chunk in chunks for e in chunk]})
    restored = rebuild(ledger, journal=assembled)
    assert snapshot(ledger=restored).ref() == snapshot(ledger=ledger).ref()


def test_replacement_history_does_not_change_the_sealed_result_digest():
    case, refs, ledger = fixture()
    incremental = ledger.replace((assessment(case["replacement"], refs),))
    batch = IntervalLedger(
        journal=ledger.journal, points=ledger.points, intervals=incremental.intervals
    )
    assert snapshot(ledger=batch).ref() == snapshot(ledger=incremental).ref()


def test_unknown_walking_duration_is_not_silently_dropped_from_average_denominator():
    _, _, ledger = fixture("first_fix_after_three_minutes")
    events = list(ledger.journal.events)
    events[2] = rebuild(events[2], elapsed_ns=None)
    changed = rebuild(ledger, journal=EvidenceJournal(events=tuple(events)))
    assert changed.metrics().walking_distance_m == 12
    assert changed.metrics().included_moving_unknown_duration_intervals == 1
    assert changed.metrics().average_walking_speed_mps is None


def test_no_gps_walk_keeps_control_time_and_zero_observed_still_time():
    _, refs, ledger = fixture("first_fix_after_three_minutes")
    journal = EvidenceJournal(events=(ledger.journal.events[0], ledger.journal.events[-1]))
    interval = rebuild(
        ledger.intervals[0], source_range=SourceRange(start=refs["start"], end=refs["end"])
    )
    empty = IntervalLedger(journal=journal, points=(), intervals=(interval,))
    assert empty.metrics().known_duration_ns == 200_000_000_000
    assert empty.metrics().observed_still_duration_ns == 0
    assert empty.metrics().average_walking_speed_mps is None


def test_paused_observations_do_not_reconnect_and_pause_is_not_still_time():
    _, _, original = fixture("walking_excluded_gap_still_restart")
    events = list(original.journal.events)
    events[2] = rebuild(events[2], kind="pause")
    events[4] = rebuild(events[4], kind="observation")
    events[6] = rebuild(events[6], kind="resume")
    journal = EvidenceJournal(events=tuple(events))
    from daengs_walk.trajectory import PointAssessment

    points = tuple(
        PointAssessment(ref=e.ref, position_quality="usable", reasons=("fixture",))
        for e in events
        if e.kind == "observation"
    )
    intervals = tuple(
        rebuild(
            i, continuity="broken", walking_use="excluded", activity="unknown", walking_distance_m=0
        )
        for i in original.intervals
    )
    paused = IntervalLedger(journal=journal, points=points, intervals=intervals)
    assert paused.metrics().paused_duration_ns == 41_000_000_000
    assert paused.metrics().observed_still_duration_ns == 0
    attempted = list(intervals)
    attempted[4] = rebuild(attempted[4], continuity="connected")
    with pytest.raises(ValueError, match="connection cannot erase"):
        rebuild(paused, intervals=attempted)


@pytest.mark.parametrize("change", ["reordered", "duplicate", "other_session", "clock_backwards"])
def test_journal_rejects_corrupted_reassembly(change):
    _, _, ledger = fixture()
    events = list(ledger.journal.events)
    if change == "reordered":
        events[1], events[2] = events[2], events[1]
    elif change == "duplicate":
        events.insert(1, events[1])
    elif change == "other_session":
        events[1] = rebuild(events[1], ref=rebuild(events[1].ref, session_id="other-walk"))
    else:
        events[2] = rebuild(events[2], elapsed_ns=0)
    with pytest.raises(ValueError):
        EvidenceJournal(events=tuple(events))
