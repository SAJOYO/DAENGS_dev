"""Separate writer contracts: spatial history cannot enter current-action input."""

from typing import Literal

from pydantic import Field, JsonValue, model_validator

from daengs_walk.diary.contracts.input import DiaryContract, digest

VERSION = "relational-diary-skeleton-v2"


class SpatialMaterial(DiaryContract):
    id: str
    role: str
    material: dict[str, JsonValue]
    relation: str
    time_meaning: str | None = None


class RoadReference(DiaryContract):
    id: str
    road_nm: str
    scope: str


class SpatialRelation(DiaryContract):
    id: str
    kind: Literal["point_difference", "matching_points", "introduced", "unconfirmed"]
    role: str
    before: dict[str, JsonValue] | None = None
    after: dict[str, JsonValue] | None = None
    source_ids: tuple[str, ...]
    scope: str = "두 기록점의 설명 비교. 중간의 연속성·경계 통과·방문은 미확인"


class SpaceInput(DiaryContract):
    mode: Literal["initial", "change", "reintroduce"]
    current_space: tuple[SpatialMaterial, ...]
    relations: tuple[SpatialRelation, ...]
    required_relation_ids: tuple[str, ...]
    road_reference: RoadReference | None = None
    narration: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def relation_required(self):
        if not set(self.required_relation_ids) <= {r.id for r in self.relations}:
            raise ValueError("spatial plan lost required relation")
        if self.mode == "change" and not self.required_relation_ids:
            raise ValueError("changed spatial plan must retain its relation")
        return self


class RecordedAction(DiaryContract):
    id: str
    actor: str | None
    action: Literal["냄새 맡기", "배변", "배설", "짖기"]


class PinMovement(DiaryContract):
    id: str
    meaning: str
    relation: str


class ActionInput(DiaryContract):
    recorded_action: RecordedAction
    pin_at: str
    current_space: tuple[SpatialMaterial, ...]
    road_reference: RoadReference | None = None
    movement_context: PinMovement | None = None
    narration: dict[str, JsonValue] | None = None


class WriterTask(DiaryContract):
    id: str
    scene_id: str
    stage: Literal["space", "action"]
    payload: dict[str, JsonValue]
    revision: str

    @model_validator(mode="after")
    def check_input_boundary(self):
        contract = SpaceInput if self.stage == "space" else ActionInput
        contract.model_validate(self.payload)
        if digest([self.stage, self.scene_id, self.payload]) != self.revision:
            raise ValueError("writer task changed")
        return self


class WriterAnswer(DiaryContract):
    text: str = Field(min_length=1, max_length=220)
    evidence_ids: tuple[str, ...]


def writer_task(stage, scene_id, payload):
    data = payload.model_dump(mode="json")
    return WriterTask(
        id=f"{stage}:{scene_id}",
        scene_id=scene_id,
        stage=stage,
        payload=data,
        revision=digest([stage, scene_id, data]),
    ).model_dump(mode="json")
