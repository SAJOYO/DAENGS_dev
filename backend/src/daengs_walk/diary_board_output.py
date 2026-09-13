"""Public projection of the internal base board: no pin payload or private source snapshot."""

from typing import Literal

from pydantic import Field, model_validator

from daengs_walk.diary_board import (
    BaseBoard,
    BoundaryCore,
    CheckpointCore,
    ObservationCore,
    RecordCore,
)
from daengs_walk.diary_card_narrative import CardNarrative, content_revision
from daengs_walk.diary_input import (
    Anchor,
    DiaryContract,
    Digest,
    Identifier,
    MaterialRef,
    MovementObservation,
    RecordContent,
    digest,
)
from daengs_walk.diary_output import BackgroundPiece

BOARD_FORMAT = "walk-diary-board-v1"
BOARD_RESPONSE = "walk-diary-board-response-v1"


class RouteCheckpoint(DiaryContract):
    analysis_id: Identifier
    block: int = Field(ge=0)
    route_m: float = Field(ge=0)


class PublishedBoardScene(DiaryContract):
    id: Identifier
    order: int = Field(ge=1)
    core: MaterialRef
    kind: Literal["user_record", "movement_observation", "route_checkpoint", "session_boundary"]
    anchor: Anchor
    title: str = Field(min_length=1, max_length=80)
    body: str = Field(min_length=1, max_length=2400)
    user_record: RecordContent | None = None
    observation: MovementObservation | None = None
    checkpoint: RouteCheckpoint | None = None
    boundary: Literal["start", "end"] | None = None
    place_reference: tuple[BackgroundPiece, ...] = ()
    writing: CardNarrative | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def matching_core(self):
        values = {
            "user_record": self.user_record,
            "movement_observation": self.observation,
            "route_checkpoint": self.checkpoint,
            "session_boundary": self.boundary,
        }
        if {k for k, v in values.items() if v is not None} != {self.kind}:
            raise ValueError("public scene must carry exactly its declared core")
        if self.writing:
            writing = self.writing
            if (
                self.user_record
                and self.user_record.kind == "note"
                and writing.original_text != self.user_record.text
            ):
                raise ValueError("card changed the original note")
            revision = content_revision(
                self.id,
                self.anchor.model_dump(mode="json"),
                [p.model_dump(mode="json") for p in self.place_reference],
                writing.space.model_dump(mode="json"),
                [a.model_dump(mode="json") for a in writing.actions],
                writing.original_text,
            )
            if writing.body() != self.body or revision != writing.content_revision:
                raise ValueError("card body differs from its adopted parts")
            behavior = self.user_record is not None and self.user_record.kind == "behavior"
            if bool(writing.actions) != behavior:
                raise ValueError("action writing requires a behavior pin")
            for action in writing.actions:
                if (
                    action.action_id != "action:" + digest(self.core)
                    or action.actor_id != self.user_record.pet_id
                ):
                    raise ValueError("action actor/core changed")
        return self


class PublishedBoard(DiaryContract):
    format: Literal["walk-diary-board-v1"] = BOARD_FORMAT
    client_session_id: Identifier
    input_revision: Digest
    plan_revision: Digest
    title: str = Field(min_length=1, max_length=80)
    model_status: Literal["accepted", "not_requested", "unavailable"]
    failure_code: (
        Literal["provider_failed", "invalid_response", "interrupted", "budget_exceeded"] | None
    )
    scenes: tuple[PublishedBoardScene, ...] = Field(min_length=2, max_length=602)

    @model_validator(mode="after")
    def consistent(self):
        if [s.order for s in self.scenes] != list(range(1, len(self.scenes) + 1)) or len(
            {s.id for s in self.scenes}
        ) != len(self.scenes):
            raise ValueError("invalid board scene sequence")
        if (self.model_status == "unavailable") != (self.failure_code is not None):
            raise ValueError("failed writing requires a failure code")
        if self.scenes[0].boundary != "start" or self.scenes[-1].boundary != "end":
            raise ValueError("board must keep its start/end boundaries")
        return self


def publish_board(board: BaseBoard, plan) -> PublishedBoard:
    if board.plan_revision != plan.revision():
        raise ValueError("public board requires its prepared plan")
    backgrounds = {s.id: s.background for s in plan.stamps}
    scenes = []
    for scene in board.scenes:
        core = scene.core
        scenes.append(
            PublishedBoardScene(
                id=scene.id,
                order=scene.order,
                core=scene.core_ref,
                kind=core.kind,
                anchor=scene.anchor,
                title=scene.title,
                body=scene.body,
                user_record=core.record.content if isinstance(core, RecordCore) else None,
                observation=core.observation if isinstance(core, ObservationCore) else None,
                checkpoint=RouteCheckpoint(
                    analysis_id=core.route.analysis_id, block=core.block, route_m=core.route_m
                )
                if isinstance(core, CheckpointCore)
                else None,
                boundary=core.boundary if isinstance(core, BoundaryCore) else None,
                place_reference=tuple(
                    p for p in backgrounds[scene.id] if p.kind == "place_reference"
                ),
            )
        )
    return PublishedBoard(
        client_session_id=board.client_session_id,
        input_revision=board.input_revision,
        plan_revision=board.plan_revision,
        title=board.title,
        model_status=board.model_status,
        failure_code=board.failure_code,
        scenes=tuple(scenes),
    )
