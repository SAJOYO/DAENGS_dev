"""JSON fixture aliases resolve to durable refs before reaching production contracts."""

import json
from pathlib import Path

from daengs_walk.trajectory import (
    EvidenceJournal,
    IntervalAssessment,
    IntervalLedger,
    JournalEvent,
    PointAssessment,
    SourceRange,
    SourceRef,
)
from daengs_walk.trajectory_view import MeasurementKey, MeasurementSnapshot, WalkScope

CASES = json.loads(
    (Path(__file__).parents[1] / "fixtures/trajectory-contract-v1.json").read_text(encoding="utf-8")
)["cases"]


def fixture(name="outlier_bypass"):
    case = next(case for case in CASES if case["name"] == name)
    refs = {
        event["id"]: SourceRef(
            session_id="synthetic-walk",
            source_epoch=event.get("epoch", "boot1"),
            clock_epoch_id=event.get("epoch", "boot1"),
            ingress_seq=i,
        )
        for i, event in enumerate(case["events"])
    }
    journal = EvidenceJournal(
        events=tuple(
            JournalEvent(ref=refs[event["id"]], kind=event["kind"], elapsed_ns=event["ns"])
            for event in case["events"]
        )
    )
    points = tuple(
        PointAssessment(ref=e.ref, position_quality="usable", reasons=("synthetic_assessment",))
        for e in journal.events
        if e.kind == "observation"
    )
    ledger = IntervalLedger(
        journal=journal,
        points=points,
        intervals=tuple(assessment(item, refs) for item in case["intervals"]),
    )
    return case, refs, ledger


def assessment(item, refs):
    return IntervalAssessment(
        source_range=SourceRange(start=refs[item["start"]], end=refs[item["end"]]),
        **{k: v for k, v in item.items() if k not in {"start", "end"}},
    )


def rebuild(model, **changes):
    # model_copy deliberately bypasses Pydantic validation; input tests must not use it.
    return type(model).model_validate({**model.model_dump(), **changes})


def snapshot(measurement_id="local-R1", ledger=None, **changes):
    if ledger is None:
        case, refs, initial = fixture()
        ledger = initial.replace((assessment(case["replacement"], refs),))
    return MeasurementSnapshot(
        **{
            "scope": WalkScope(owner_id="synthetic-owner", session_id="synthetic-walk"),
            "measurement_id": measurement_id,
            "source": "device",
            "key": MeasurementKey(
                input_fingerprint="a" * 64,
                journal_fingerprint="b" * 64,
                clock_mapping_version="monotonic-only-v1",
                coordinate_basis="fixture",
                precision_fingerprint=None,
                motion_policy_version="synthetic-adjudicated-v1",
                config_hash="c" * 64,
                connectivity_policy_version="synthetic-reviewed-v1",
                engine_version="contract-fixture-v1",
            ),
            "ledger": ledger,
            **changes,
        }
    )
