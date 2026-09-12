"""Final interval ownership and source time addresses; no GPS policy, IO or storage.

The journal includes control events. A source range owns the edges [start, end),
not the endpoint observations. Display slices never enter this accounting ledger.
"""

from __future__ import annotations

from functools import cached_property
from math import fsum
from types import MappingProxyType
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=200)]
Nonnegative = Annotated[float, Field(ge=0)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class SourceRef(Contract):
    session_id: Identifier
    source_epoch: Identifier
    clock_epoch_id: Identifier
    ingress_seq: int | None = Field(default=None, ge=0)
    control_kind: Literal["epoch_start", "epoch_end"] | None = None

    @model_validator(mode="after")
    def identity(self) -> Self:
        if (self.ingress_seq is None) != (self.control_kind is not None):
            raise ValueError("source reference is an ingress record or an epoch control")
        return self


class SourceRange(Contract):
    start: SourceRef
    end: SourceRef

    @model_validator(mode="after")
    def same_session(self) -> Self:
        if self.start.session_id != self.end.session_id or self.start == self.end:
            raise ValueError("range requires distinct boundaries in one session")
        return self


class JournalEvent(Contract):
    ref: SourceRef
    kind: Literal["start", "observation", "pause", "resume", "loss", "end"]
    elapsed_ns: int | None = Field(default=None, ge=0)
    original_elapsed_ns: int | None = Field(default=None, ge=0)
    time_reasons: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def control_identity(self) -> Self:
        expected = {
            "start": "epoch_start",
            "resume": "epoch_start",
            "pause": "epoch_end",
            "end": "epoch_end",
        }
        if self.ref.control_kind is not None and self.ref.control_kind != expected.get(self.kind):
            raise ValueError("epoch control reference does not match event kind")
        if self.original_elapsed_ns is not None and self.elapsed_ns not in (
            None,
            self.original_elapsed_ns,
        ):
            raise ValueError("timeline cannot rewrite an original sample time")
        return self


class PointAssessment(Contract):
    ref: SourceRef
    position_quality: Literal["usable", "uncertain", "invalid"]
    reasons: tuple[Identifier, ...] = Field(min_length=1)


class EvidenceJournal(Contract):
    events: tuple[JournalEvent, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.events[0].kind != "start" or self.events[-1].kind != "end":
            raise ValueError("sealed journal requires start and end controls")
        session = self.events[0].ref.session_id
        if len({e.ref for e in self.events}) != len(self.events):
            raise ValueError("journal source references must be unique")
        sequences: dict[str, int] = {}
        clocks: dict[str, int] = {}
        seen_sources: set[str] = set()
        seen_clocks: set[str] = set()
        previous = None
        paused = False
        for i, event in enumerate(self.events):
            ref = event.ref
            if ref.session_id != session:
                raise ValueError("journal crosses sessions")
            if ref.ingress_seq is not None and ref.ingress_seq <= sequences.get(
                ref.source_epoch, -1
            ):
                raise ValueError("source sequence must increase, including controls")
            if previous is not None:
                if ref.source_epoch != previous.source_epoch and ref.source_epoch in seen_sources:
                    raise ValueError("source epoch cannot reappear")
                if (
                    ref.clock_epoch_id != previous.clock_epoch_id
                    and ref.clock_epoch_id in seen_clocks
                ):
                    raise ValueError("clock epoch cannot reappear")
            if event.elapsed_ns is not None:
                if event.elapsed_ns < clocks.get(ref.clock_epoch_id, 0):
                    raise ValueError("monotonic clock moved backwards within its epoch")
                clocks[ref.clock_epoch_id] = event.elapsed_ns
            if event.kind in {"start", "end"} and i not in {0, len(self.events) - 1}:
                raise ValueError("session controls cannot occur inside a sealed journal")
            if event.kind == "pause":
                if paused:
                    raise ValueError("duplicate pause")
                paused = True
            elif event.kind == "resume":
                if not paused:
                    raise ValueError("resume requires pause")
                paused = False
            if ref.ingress_seq is not None:
                sequences[ref.source_epoch] = ref.ingress_seq
            seen_sources.add(ref.source_epoch)
            seen_clocks.add(ref.clock_epoch_id)
            previous = ref
        return self

    @cached_property
    def _indexes(self):
        return MappingProxyType({event.ref: i for i, event in enumerate(self.events)})

    def indexes(self):
        return self._indexes

    def bounds(self, span: SourceRange) -> tuple[int, int]:
        indexes = self.indexes()
        try:
            start, end = indexes[span.start], indexes[span.end]
        except KeyError as error:
            raise ValueError("unknown source boundary") from error
        if start >= end:
            raise ValueError("source range must follow journal order")
        return start, end

    def duration_ns(self, span: SourceRange) -> int | None:
        start, end = self.bounds(span)
        left, right = self.events[start], self.events[end]
        if (
            left.ref.clock_epoch_id != right.ref.clock_epoch_id
            or left.elapsed_ns is None
            or right.elapsed_ns is None
        ):
            return None
        return right.elapsed_ns - left.elapsed_ns


class IntervalAssessment(Contract):
    source_range: SourceRange
    continuity: Literal["connected", "broken", "unresolved"]
    walking_use: Literal["included", "excluded", "unresolved"]
    activity: Literal["moving", "still", "unknown"] = "unknown"
    measured_displacement_m: Nonnegative | None = None
    walking_distance_m: Nonnegative = 0
    reasons: tuple[Identifier, ...] = Field(min_length=1)
    reassessment_basis: Identifier | None = None
    reentry_basis: Identifier | None = None

    @model_validator(mode="after")
    def contributions(self) -> Self:
        if self.walking_use == "included" and self.continuity != "connected":
            raise ValueError("walking inclusion requires a connected interval")
        if self.walking_use != "included" and self.walking_distance_m != 0:
            raise ValueError("only included intervals own walking distance")
        if self.continuity != "connected" and self.activity != "unknown":
            raise ValueError("missing continuity cannot assert moving or still time")
        if self.walking_distance_m > 0 and self.activity != "moving":
            raise ValueError("positive walking distance requires moving evidence")
        return self


class LedgerMetrics(Contract):
    walking_distance_m: Nonnegative
    known_duration_ns: int = Field(ge=0)
    located_duration_ns: int = Field(ge=0)
    included_moving_duration_ns: int = Field(ge=0)
    observed_still_duration_ns: int = Field(ge=0)
    paused_duration_ns: int = Field(ge=0)
    unlocated_duration_ns: int = Field(ge=0)
    unresolved_walking_duration_ns: int = Field(ge=0)
    unknown_duration_intervals: int = Field(ge=0)
    included_moving_unknown_duration_intervals: int = Field(ge=0)

    @property
    def average_walking_speed_mps(self) -> float | None:
        # Unknown duration cannot silently shrink the denominator.
        if self.included_moving_unknown_duration_intervals or not self.included_moving_duration_ns:
            return None
        return self.walking_distance_m / (self.included_moving_duration_ns / 1e9)

    @property
    def located_fraction_of_known_time(self) -> float | None:
        return self.located_duration_ns / self.known_duration_ns if self.known_duration_ns else None


class IntervalLedger(Contract):
    journal: EvidenceJournal
    points: tuple[PointAssessment, ...]
    intervals: tuple[IntervalAssessment, ...] = Field(min_length=1)
    superseded: tuple[IntervalAssessment, ...] = ()

    @model_validator(mode="after")
    def partition(self) -> Self:
        indexes = self.journal.indexes()
        quality = {point.ref: point.position_quality for point in self.points}
        observation_refs = tuple(e.ref for e in self.journal.events if e.kind == "observation")
        if tuple(p.ref for p in self.points) != observation_refs:
            raise ValueError("every observation requires one assessment in journal order")
        paused_edges: set[int] = set()
        paused = False
        for i, event in enumerate(self.journal.events[:-1]):
            if event.kind == "pause":
                paused = True
            elif event.kind == "resume":
                paused = False
            if paused:
                paused_edges.add(i)
        cursor = 0
        for number, interval in enumerate(self.intervals):
            span = interval.source_range
            start, end = indexes.get(span.start), indexes.get(span.end)
            if start != cursor or end is None or end <= start:
                raise ValueError(
                    "final intervals must partition source edges without holes/overlap"
                )
            events = self.journal.events[start : end + 1]
            if any(e.kind != "observation" for e in events[1:-1]):
                raise ValueError("control events must remain final interval boundaries")
            if len(events) > 2 and (
                len({e.ref.source_epoch for e in events}) != 1
                or len({e.ref.clock_epoch_id for e in events}) != 1
            ):
                raise ValueError("epoch boundaries cannot absorb neighboring known time")
            if interval.continuity == "connected":
                if (
                    quality.get(events[0].ref) != "usable"
                    or quality.get(events[-1].ref) != "usable"
                    or any(e.kind != "observation" for e in events)
                    or len({e.ref.source_epoch for e in events}) != 1
                    or len({e.ref.clock_epoch_id for e in events}) != 1
                    or any(i in paused_edges for i in range(start, end))
                ):
                    raise ValueError(
                        "connection cannot erase controls, epochs or unusable endpoints"
                    )
                if end - start > 1 and interval.reassessment_basis is None:
                    raise ValueError("bypassed observations require explicit reassessment evidence")
                if self.journal.duration_ns(span) == 0 and interval.walking_distance_m > 0:
                    raise ValueError("positive distance cannot have zero duration")
            if (
                interval.walking_use == "included"
                and number > 0
                and self.intervals[number - 1].walking_use != "included"
                and interval.reentry_basis is None
            ):
                raise ValueError("walking entry or reentry requires evidence")
            cursor = end
        if cursor != len(self.journal.events) - 1:
            raise ValueError("final intervals must cover the complete sealed journal")
        for candidate in self.superseded:
            self.journal.bounds(candidate.source_range)
        return self

    def replace(self, replacements: tuple[IntervalAssessment, ...]) -> IntervalLedger:
        """Replace whole final intervals; never prorate a partially covered distance."""
        if not replacements:
            raise ValueError("replacement cannot be empty")
        start, _ = self.journal.bounds(replacements[0].source_range)
        _, end = self.journal.bounds(replacements[-1].source_range)
        bounds = [self.journal.bounds(i.source_range) for i in self.intervals]
        if start not in {a for a, _ in bounds} or end not in {b for _, b in bounds}:
            raise ValueError("replacement must align with existing ownership boundaries")
        before, removed, after = [], [], []
        for interval, (a, b) in zip(self.intervals, bounds, strict=True):
            (before if b <= start else after if a >= end else removed).append(interval)
        return IntervalLedger(
            journal=self.journal,
            points=self.points,
            intervals=(*before, *replacements, *after),
            superseded=(*self.superseded, *removed),
        )

    def metrics(self) -> LedgerMetrics:
        known = located = moving = still = unlocated = unresolved = unknown = moving_unknown = 0
        paused = False
        paused_duration = 0
        indexes = self.journal.indexes()
        for interval in self.intervals:
            kind = self.journal.events[indexes[interval.source_range.start]].kind
            if kind == "pause":
                paused = True
            elif kind == "resume":
                paused = False
            duration = self.journal.duration_ns(interval.source_range)
            included_moving = interval.walking_use == "included" and interval.activity == "moving"
            if duration is None:
                unknown += 1
                moving_unknown += included_moving
                continue
            known += duration
            if paused:
                paused_duration += duration
            if interval.continuity == "connected":
                located += duration
            else:
                unlocated += duration
            if included_moving:
                moving += duration
            if interval.activity == "still":
                still += duration
            if interval.walking_use == "unresolved":
                unresolved += duration
        return LedgerMetrics(
            walking_distance_m=fsum(i.walking_distance_m for i in self.intervals),
            known_duration_ns=known,
            located_duration_ns=located,
            included_moving_duration_ns=moving,
            observed_still_duration_ns=still,
            paused_duration_ns=paused_duration,
            unlocated_duration_ns=unlocated,
            unresolved_walking_duration_ns=unresolved,
            unknown_duration_intervals=unknown,
            included_moving_unknown_duration_intervals=moving_unknown,
        )
