"""Time selection exists even when there is no observed map position."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from daengs_walk.trajectory import Contract, EvidenceJournal, Identifier, SourceRange, SourceRef


class EventTime(Contract):
    kind: Literal["event"] = "event"
    ref: SourceRef


class SpanTime(Contract):
    kind: Literal["elapsed"] = "elapsed"
    source_range: SourceRange
    offset_ns: int = Field(ge=0)


class UnknownTime(Contract):
    kind: Literal["unknown_span"] = "unknown_span"
    source_range: SourceRange


TimeAddress = Annotated[EventTime | SpanTime | UnknownTime, Field(discriminator="kind")]


def validate_time_address(journal: EvidenceJournal, address: TimeAddress) -> None:
    if isinstance(address, EventTime):
        if address.ref not in journal.indexes():
            raise ValueError("unknown event time address")
        return
    duration = journal.duration_ns(address.source_range)
    if isinstance(address, UnknownTime):
        if duration is not None:
            raise ValueError("known time span must use an elapsed address")
    elif duration is None or address.offset_ns > duration:
        raise ValueError("elapsed address requires a known duration and an in-range offset")


class OverviewSelection(Contract):
    mode: Literal["overview"] = "overview"


class SceneSelection(Contract):
    mode: Literal["scene"] = "scene"
    event_id: Identifier


class PassageSelection(Contract):
    mode: Literal["passage"] = "passage"
    source_ranges: tuple[SourceRange, ...] = Field(min_length=1)


class RangeSelection(Contract):
    mode: Literal["range"] = "range"
    source_ranges: tuple[SourceRange, ...] = Field(min_length=1)


class ReplaySelection(Contract):
    mode: Literal["replay"] = "replay"
    cursor: TimeAddress
    playback: Literal["paused", "playing"] = "paused"

    @model_validator(mode="after")
    def unknown_duration(self) -> Self:
        if isinstance(self.cursor, UnknownTime) and self.playback == "playing":
            raise ValueError("unknown duration can be selected but not timed for playback")
        return self


SelectionTarget = Annotated[
    OverviewSelection | SceneSelection | PassageSelection | RangeSelection | ReplaySelection,
    Field(discriminator="mode"),
]


class WalkSelection(Contract):
    session_id: Identifier
    measurement_id: Identifier
    target: SelectionTarget

    @model_validator(mode="after")
    def source_scope(self) -> Self:
        refs = []
        if isinstance(self.target, (PassageSelection, RangeSelection)):
            refs = [span.start for span in self.target.source_ranges]
        elif isinstance(self.target, ReplaySelection):
            cursor = self.target.cursor
            refs = [cursor.ref if isinstance(cursor, EventTime) else cursor.source_range.start]
        if any(ref.session_id != self.session_id for ref in refs):
            raise ValueError("selection source belongs to another session")
        return self

    def validate_against(self, journal: EvidenceJournal) -> None:
        if self.session_id != journal.events[0].ref.session_id:
            raise ValueError("selection belongs to another journal")
        if isinstance(self.target, ReplaySelection):
            validate_time_address(journal, self.target.cursor)
        elif isinstance(self.target, (PassageSelection, RangeSelection)):
            for span in self.target.source_ranges:
                journal.bounds(span)
        # Scene identity is resolved by the scene store, never inferred from GPS proximity.
