"""Separate writer contracts: spatial history cannot enter current-action input."""

from math import isfinite
from typing import Literal

from pydantic import Field, JsonValue, StrictBool, model_validator

from daengs_walk.value_contracts import ValueContract as DiaryContract
from daengs_walk.value_contracts import digest

VERSION = "relational-diary-skeleton-v6"


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


class RecordPointObservation(DiaryContract):
    record_point_id: str
    recorded_at: str
    coordinate: dict[str, JsonValue] | None = None
    observation: SpatialMaterial | None = None


class SpatialRelation(DiaryContract):
    id: str
    kind: Literal[
        "location_contrast",
        "record_context_contrast",
        "temporal_contrast",
        "deferred",
        "matching_points",
        "introduced",
        "unconfirmed",
    ]
    role: str
    earlier_record_point: RecordPointObservation | None = None
    current_record_point: RecordPointObservation
    comparison_axis: Literal["location", "record_location", "observation_time"] | None = None
    comparison_basis: dict[str, JsonValue] = Field(default_factory=dict)
    source_ids: tuple[str, ...]
    scope: str = "두 기록점의 설명 비교. 중간의 연속성·경계 통과·방문은 미확인"


class RecordChronology(DiaryContract):
    same_walk: bool | None
    earlier_recorded_at: str
    current_recorded_at: str
    elapsed_record_seconds: float | None = Field(ge=0)
    location_relationship: Literal[
        "distinct_locations", "distinct_record_locations", "same_source_subject"
    ]
    same_source_observation_time: bool | None
    time_scope: str = "기록 사이의 경과 시간과 공간 자료의 관측 시점은 별개의 시간이다"
    interval_evidence: str = "두 기록점만 비교하며 중간 경로·경계 통과는 확인하지 않음"


class WritingRelation(DiaryContract):
    id: str
    comparison_axis: Literal["location", "record_location", "observation_time"]
    attribute: str
    subjects: tuple[dict[str, JsonValue], dict[str, JsonValue]]
    scope: str
    record_chronology: RecordChronology | None = None


class SpaceInput(DiaryContract):
    mode: Literal["current_context", "express_relation", "spatial_journey"]
    current_space: tuple[SpatialMaterial, ...]
    relations: tuple[WritingRelation, ...]
    required_relation_ids: tuple[str, ...]
    road_reference: RoadReference | None = None
    narration: dict[str, JsonValue] | None = None
    journey: dict[str, JsonValue] | None = None
    short_memory: tuple[dict[str, JsonValue], ...] = Field(default=(), max_length=2)

    @model_validator(mode="after")
    def relation_required(self):
        for memory in self.short_memory:
            if set(memory) - {
                "recorded_at",
                "confirmed_context",
                "planned_focus",
                "publication_status",
                "meaning_delivery",
            }:
                raise ValueError("memory may contain facts and delivery metadata only")
            for fact in memory.get("confirmed_context", []):
                if set(fact) - {"role", "value", "scope", "time_meaning"}:
                    raise ValueError("memory must not contain generated prose or actions")
        if not set(self.required_relation_ids) <= {r.id for r in self.relations}:
            raise ValueError("spatial plan lost required relation")
        if self.mode == "express_relation" and not self.required_relation_ids:
            raise ValueError("changed spatial plan must retain its relation")
        if self.mode == "spatial_journey" and (
            not self.journey or self.journey.get("connection") != "connected"
        ):
            raise ValueError("spatial journey needs connected canonical observations")
        if self.mode == "spatial_journey":
            numbers = [
                self.journey.get(k)
                for k in (
                    "elapsed_seconds",
                    "observed_seconds",
                    "observed_distance_m",
                    "moving_distance_m",
                )
            ]
            if not all(
                isinstance(v, (float, int)) and not isinstance(v, bool) and isfinite(v) and v >= 0
                for v in numbers
            ):
                raise ValueError("journey metrics must be finite measured values")
            elapsed, observed, distance, moving = numbers
            if (
                not elapsed
                or abs(elapsed - observed) > 1e-6
                or moving > distance
                or self.journey.get("uncovered_intervals")
            ):
                raise ValueError("connected journey cannot have uncovered time")
        return self


class RecordedAction(DiaryContract):
    id: str
    actor: str | None
    action: Literal["냄새 맡기", "배변", "배설", "짖기"]


class PinMovement(DiaryContract):
    id: str
    meaning: str
    relation: str


class CurrentMotion(DiaryContract):
    id: str
    meaning: str
    from_pin_s: float = Field(allow_inf_nan=False)
    to_pin_s: float = Field(allow_inf_nan=False)
    event_at_pin_s: float | None = Field(default=None, allow_inf_nan=False)
    relation: str

    @model_validator(mode="after")
    def contains_pin(self):
        if not self.from_pin_s <= 0 < self.to_pin_s or self.event_at_pin_s not in (None, 0):
            raise ValueError('action motion must contain the current pin')
        return self


class ActionInput(DiaryContract):
    recorded_action: RecordedAction
    pin_at: str
    current_space: tuple[SpatialMaterial, ...]
    road_reference: RoadReference | None = None
    movement_context: PinMovement | None = None
    current_gait: tuple[CurrentMotion, ...] = ()
    current_shape: tuple[CurrentMotion, ...] = ()
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
        if self.stage == "space" and self.payload.get("version") == "scene-comparison-v1":
            from daengs_walk.diary.relational.scene_comparison_contracts import SpaceComparisonInput

            contract = SpaceComparisonInput
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


class SemanticReview(DiaryContract):
    """A model assessment, not a mathematical proof of natural-language truth."""

    supported: StrictBool
    preserves_subjects: StrictBool
    preserves_relation_axis: StrictBool
    preserves_scope: StrictBool
    no_invented_experience: StrictBool
    required_meanings_present: StrictBool
    readable_as_diary: StrictBool
    used_evidence_ids: tuple[str, ...]
    issues: tuple[str, ...]

    @property
    def passes(self) -> bool:
        return (
            all(
                (
                    self.supported,
                    self.preserves_subjects,
                    self.preserves_relation_axis,
                    self.preserves_scope,
                    self.no_invented_experience,
                    self.required_meanings_present,
                    self.readable_as_diary,
                )
            )
            and not self.issues
        )
