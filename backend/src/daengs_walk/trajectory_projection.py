"""Read-only paths and endpoint semantics derived from final interval ownership."""

import hashlib
import json
from math import fsum
from typing import Literal

from pydantic import Field

from daengs_walk.trajectory import Contract, Identifier, IntervalLedger, SourceRange, SourceRef


class TrajectoryLocation(Contract):
    ref: SourceRef
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class PathSection(Contract):
    id: Identifier
    kind: Literal["observed_run", "walking_section"]
    source_ranges: tuple[SourceRange, ...]
    point_refs: tuple[SourceRef, ...]
    walking_distance_m: float = Field(ge=0)


class TrajectoryBoundaries(Contract):
    record_start: SourceRef
    record_end: SourceRef
    first_observed: SourceRef | None
    last_observed: SourceRef | None
    first_walking: SourceRef | None
    last_walking: SourceRef | None


def path_sections(ledger: IntervalLedger, *, walking_only: bool) -> tuple[PathSection, ...]:
    kind = "walking_section" if walking_only else "observed_run"
    groups = []
    current = []
    for interval in ledger.intervals:
        use = interval.continuity == "connected" and (
            not walking_only or interval.walking_use == "included"
        )
        if use:
            current.append(interval)
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    result = []
    for group in groups:
        ranges = tuple(i.source_range for i in group)
        refs = (ranges[0].start, *(span.end for span in ranges))
        identity = json.dumps([kind, [ref.model_dump() for ref in refs]], sort_keys=True)
        result.append(
            PathSection(
                id=hashlib.sha256(identity.encode()).hexdigest()[:24],
                kind=kind,
                source_ranges=ranges,
                point_refs=refs,
                walking_distance_m=fsum(i.walking_distance_m for i in group),
            )
        )
    return tuple(result)


def boundaries(ledger: IntervalLedger) -> TrajectoryBoundaries:
    usable = {p.ref for p in ledger.points if p.position_quality == "usable"}
    observed = [
        e.ref for e in ledger.journal.events if e.ref in usable and e.elapsed_ns is not None
    ]
    walking = path_sections(ledger, walking_only=True)
    return TrajectoryBoundaries(
        record_start=ledger.journal.events[0].ref,
        record_end=ledger.journal.events[-1].ref,
        first_observed=observed[0] if observed else None,
        last_observed=observed[-1] if observed else None,
        first_walking=walking[0].point_refs[0] if walking else None,
        last_walking=walking[-1].point_refs[-1] if walking else None,
    )
