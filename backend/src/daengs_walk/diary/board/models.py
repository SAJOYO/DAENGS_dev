"""Internal base-board contract. This is not the published diary-bundle-v1 API.

Selection protects original materials; assembly owns card text. New spatial and
boundary cores cannot be downcast into a user action or movement observation.
"""

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field, model_validator

from daengs_walk.diary.contracts.input import (
    Anchor,
    DiaryContract,
    Digest,
    Identifier,
    MaterialRef,
    MovementObservation,
    RouteVersion,
    UserRecord,
    digest,
)
from daengs_walk.diary.contracts.output import BackgroundPiece
from daengs_walk.diary.selection.stamps import PreparedDiary, StampPolicy
from daengs_walk.evidence import WalkEvidenceBundle


@dataclass(frozen=True)
class VerifiedBoardRoute:
    """Service-owned binding, after replaying the saved analysis against uploaded fixes."""

    version: RouteVersion
    evidence: WalkEvidenceBundle


class BaseBoardPolicy(DiaryContract):
    version: Literal["records-route-boundaries-v1"] = "records-route-boundaries-v1"
    intermediate: StampPolicy
    separation_m: float = Field(default=100, ge=20, le=500)
    # Only coverage bookkeeping uses this tolerance; original pins never move.
    record_route_tolerance_m: float = Field(default=30, ge=0, le=100)


class RecordCore(DiaryContract):
    kind: Literal["user_record"] = "user_record"
    record: UserRecord

    @model_validator(mode="after")
    def live(self):
        if self.record.deleted:
            raise ValueError("deleted record cannot centre a scene")
        return self


class ObservationCore(DiaryContract):
    kind: Literal["movement_observation"] = "movement_observation"
    observation: MovementObservation


class CheckpointCore(DiaryContract):
    kind: Literal["route_checkpoint"] = "route_checkpoint"
    route: RouteVersion
    block: int = Field(ge=0)
    route_m: float = Field(ge=0)
    anchor: Anchor

    @model_validator(mode="after")
    def observed(self):
        if self.route.status != "ready" or not exact_fix(self.anchor):
            raise ValueError("checkpoint requires one exact canonical route fix")
        return self


class BoundaryCore(DiaryContract):
    kind: Literal["session_boundary"] = "session_boundary"
    boundary: Literal["start", "end"]
    anchor: Anchor

    @model_validator(mode="after")
    def exact_or_unlocated(self):
        if self.anchor.point is None:
            if self.anchor.time_basis != "session_fallback":
                raise ValueError("unlocated boundary uses the saved session time")
        elif not exact_fix(self.anchor):
            raise ValueError("boundary position must be observed at the boundary time")
        return self


def exact_fix(anchor):
    return (
        anchor.time_basis == "route_observation"
        and anchor.method == "observed"
        and anchor.position_state == "resolved"
        and anchor.location_at == anchor.event_at
        and len(anchor.source_fixes) == 1
        and anchor.source_fixes[0].at == anchor.event_at
    )


BoardCore = Annotated[
    RecordCore | ObservationCore | CheckpointCore | BoundaryCore, Field(discriminator="kind")
]


def core_anchor(core):
    if isinstance(core, RecordCore):
        return core.record.anchor
    if isinstance(core, ObservationCore):
        return core.observation.anchor
    return core.anchor


class BoardStamp(DiaryContract):
    id: Identifier
    core_ref: MaterialRef
    core: BoardCore
    background: tuple[BackgroundPiece, ...] = Field(default=(), max_length=16)


class PreparedBaseBoard(DiaryContract):
    format: Literal["walk-diary-base-plan-v1"] = "walk-diary-base-plan-v1"
    input_revision: Digest
    route_revision: Digest | None
    policy: BaseBoardPolicy
    intermediate: PreparedDiary
    stamps: tuple[BoardStamp, ...] = Field(min_length=2, max_length=602)
    counts: dict[str, int]
    limits: tuple[str, ...]

    def revision(self):
        return digest(PreparedBaseBoard.model_validate(self.model_dump(mode="json")))


class BoardScene(DiaryContract):
    id: Identifier
    order: int = Field(ge=1)
    core_ref: MaterialRef
    core: BoardCore
    anchor: Anchor
    title: str = Field(min_length=1, max_length=80)
    # One editable body. Evidence/core separation is private provenance, not UI sections.
    body: str = Field(min_length=1, max_length=2400)

    @model_validator(mode="after")
    def original_anchor(self):
        if self.anchor != core_anchor(self.core):
            raise ValueError("card location must preserve its core anchor")
        return self


class BaseBoard(DiaryContract):
    format: Literal["walk-diary-base-board-v1"] = "walk-diary-base-board-v1"
    client_session_id: Identifier
    input_revision: Digest
    plan_revision: Digest
    title: str = Field(min_length=1, max_length=80)
    model_status: Literal["not_requested", "accepted", "unavailable"]
    failure_code: (
        Literal["provider_failed", "invalid_response", "interrupted", "budget_exceeded"] | None
    ) = None
    scenes: tuple[BoardScene, ...] = Field(min_length=2, max_length=602)

    @model_validator(mode="after")
    def sequence(self):
        if [s.order for s in self.scenes] != list(range(1, len(self.scenes) + 1)):
            raise ValueError("board order must be sequential")
        if len({s.id for s in self.scenes}) != len(self.scenes):
            raise ValueError("duplicate board scene")
        if (self.model_status == "unavailable") != (self.failure_code is not None):
            raise ValueError("failed writing requires a failure code")
        return self
