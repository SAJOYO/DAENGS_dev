"""Whole-scene comparison v1 used by preparation, writing and frozen publication.

Facts and rule-derived relationships have no narrative priority or sentence template.
Header data stays outside SpaceComparisonInput. Original notes/photos stay elsewhere.
"""

from typing import Literal

from pydantic import Field, JsonValue, model_validator

from daengs_walk.diary.relational.relations.contracts import RelationSlot
from daengs_walk.value_contracts import Instant, Point, ValueContract

Family = Literal["road", "land_cover", "surrounding_object", "area_context"]


class FactScope(ValueContract):
    kind: Literal["record_point", "registered_point", "query_area"]
    description: str = Field(min_length=1)
    # Stable geometry/query definition, not its human-facing name.
    coverage_key: str | None = None


class SceneFact(ValueContract):
    id: str = Field(min_length=1)
    family: Family
    value: dict[str, JsonValue]
    scope: FactScope
    # Provider-qualified identity. Equal names are not object identity.
    subject_key: str | None = None
    source_refs: tuple[str, ...] = Field(min_length=1)
    observed_at: Instant | None = None
    retrieved_at: Instant | None = None
    reference_date: str | None = None
    time_meaning: str = Field(min_length=1)


class SceneSnapshot(ValueContract):
    scene_id: str = Field(min_length=1)
    walk_id: str = Field(min_length=1)
    recorded_at: Instant
    point: Point | None
    accuracy_m: float | None = Field(default=None, ge=0)
    position_basis: Literal["observed", "estimated", "last_known", "none"]
    facts: tuple[SceneFact, ...]
    # Empty evidence and collection failure must remain distinguishable.
    collection: dict[
        Family, Literal["complete", "partial", "empty", "failed", "not_requested", "unknown"]
    ]
    collection_reasons: dict[Family, tuple[str, ...]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_facts(self):
        if len({f.id for f in self.facts}) != len(self.facts):
            raise ValueError("duplicate scene evidence ID")
        if (self.point is None) != (self.position_basis == "none"):
            raise ValueError("position basis does not match point availability")
        if any(f.family not in self.collection for f in self.facts):
            raise ValueError("fact family has no collection status")
        return self


class SpatialCorrespondence(ValueContract):
    id: str = Field(min_length=1)
    family: Family
    axis: Literal["record_location", "object_distance", "query_area"]
    result: Literal[
        "same_value",
        "different_values",
        "same_object",
        "only_one_snapshot_has_evidence",
        "same_query_area",
        "different_query_areas",
        "incomparable",
    ]
    earlier_evidence_ids: tuple[str, ...]
    current_evidence_ids: tuple[str, ...]
    distance_delta_m: float | None = None
    composition_values_equal: bool | None = None
    comparison_basis: dict[str, JsonValue] = Field(default_factory=dict)
    scope: str = Field(min_length=1)

    @model_validator(mode="after")
    def comparison_shape(self):
        allowed = {
            "road": ("record_location", {"same_value", "different_values"}),
            "land_cover": ("record_location", {"same_value", "different_values"}),
            "surrounding_object": ("object_distance", {"same_object"}),
            "area_context": ("query_area", {"same_query_area", "different_query_areas"}),
        }
        axis, results = allowed[self.family]
        if self.axis != axis or self.result not in results | {
            "only_one_snapshot_has_evidence",
            "incomparable",
        }:
            raise ValueError("relation family, axis and result disagree")
        present = (bool(self.earlier_evidence_ids), bool(self.current_evidence_ids))
        if self.result == "only_one_snapshot_has_evidence":
            if sum(present) != 1:
                raise ValueError("one-sided evidence requires exactly one endpoint")
        elif not all(present):
            raise ValueError("comparison requires both endpoints")
        if self.distance_delta_m is not None and self.result != "same_object":
            raise ValueError("distance delta requires the same object")
        if self.composition_values_equal is not None and self.result not in {
            "same_query_area",
            "different_query_areas",
        }:
            raise ValueError("composition comparison requires two query areas")
        return self


class SpatialComparisonSlot(RelationSlot):
    policy_version: str = "scene-comparison-v1"
    items: tuple[SpatialCorrespondence, ...] = ()


class SpatialComparisonSlots(ValueContract):
    background: SpatialComparisonSlot
    proximity: SpatialComparisonSlot
    area_context: SpatialComparisonSlot

    def all_relations(self):
        return self.background.items + self.proximity.items + self.area_context.items

    @model_validator(mode="after")
    def check_families(self):
        for slot, families in (
            (self.background, {"road", "land_cover"}),
            (self.proximity, {"surrounding_object"}),
            (self.area_context, {"area_context"}),
        ):
            if any(item.family not in families for item in slot.items):
                raise ValueError("relation placed in wrong slot")
        return self


class SceneConnection(ValueContract):
    earlier_scene_id: str
    current_scene_id: str
    elapsed_seconds: float = Field(ge=0)
    route_status: Literal["connected", "partial", "unavailable"]
    route_evidence_ids: tuple[str, ...] = ()
    scope: str = Field(min_length=1)

    @model_validator(mode="after")
    def route_support(self):
        if (self.route_status == "unavailable") != (not self.route_evidence_ids):
            raise ValueError("route status and evidence disagree")
        return self


class SpaceComparisonInput(ValueContract):
    version: Literal["scene-comparison-v1"] = "scene-comparison-v1"
    current: SceneSnapshot
    earlier: SceneSnapshot | None
    connection: SceneConnection | None
    relation_slots: SpatialComparisonSlots
    narration: dict[str, JsonValue] | None = None
    route_evidence: dict[str, JsonValue] | None = None

    @property
    def citation_ids(self) -> tuple[str, ...]:
        scenes = (self.earlier, self.current) if self.earlier else (self.current,)
        return tuple(f.id for s in scenes for f in s.facts) + (
            self.connection.route_evidence_ids if self.connection else ()
        )

    @model_validator(mode="after")
    def references_and_chronology(self):
        relations = self.relation_slots.all_relations()
        route_ids = self.connection.route_evidence_ids if self.connection else ()
        if bool(route_ids) != bool(self.route_evidence):
            raise ValueError("route evidence must accompany its references")
        if self.route_evidence and set(route_ids) != {self.route_evidence.get("id")}:
            raise ValueError("route evidence identity mismatch")
        if self.earlier is None:
            if self.connection is not None or relations:
                raise ValueError("initial scene cannot compare an absent earlier scene")
        else:
            a, b, c = self.earlier, self.current, self.connection
            if a.walk_id != b.walk_id or a.scene_id == b.scene_id:
                raise ValueError("comparison needs distinct scenes in the same walk")
            elapsed = (b.recorded_at - a.recorded_at).total_seconds()
            if elapsed < 0 or c is None or abs(elapsed - c.elapsed_seconds) > 1e-6:
                raise ValueError("connection must preserve scene chronology")
            if elapsed == 0 and (c.route_status != "unavailable" or self.route_evidence):
                raise ValueError("simultaneous records cannot establish intervening movement")
            if (c.earlier_scene_id, c.current_scene_id) != (a.scene_id, b.scene_id):
                raise ValueError("connection points to different scenes")
        left = {f.id: f for f in self.earlier.facts} if self.earlier else {}
        right = {f.id: f for f in self.current.facts}
        all_ids = self.citation_ids + tuple(r.id for r in relations)
        if len(set(all_ids)) != len(all_ids):
            raise ValueError("citation and relation IDs must be globally unique")
        for relation in relations:
            ends = []
            for ids, facts in (
                (relation.earlier_evidence_ids, left),
                (relation.current_evidence_ids, right),
            ):
                if not set(ids) <= facts.keys():
                    raise ValueError("relation evidence belongs to wrong or missing scene")
                selected = [facts[i] for i in ids]
                if any(f.family != relation.family for f in selected):
                    raise ValueError("relation evidence family mismatch")
                ends.extend(selected)
            if relation.result == "same_object" and (
                any(f.subject_key is None for f in ends) or len({f.subject_key for f in ends}) != 1
            ):
                raise ValueError("same object requires provider-qualified identity")
        return self


class SpaceComparisonAnswer(ValueContract):
    focus: str = Field(min_length=1, max_length=160)
    relation_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    text: str = Field(min_length=1, max_length=220)

    def validate_against(self, request: SpaceComparisonInput):
        """Check references only; focus/IDs do not prove the prose is supported."""
        if not set(self.relation_ids) <= {r.id for r in request.relation_slots.all_relations()}:
            raise ValueError("unknown selected relation")
        if not set(self.evidence_ids) <= set(request.citation_ids):
            raise ValueError("unknown cited evidence")
        return self


class SceneCardHeader(ValueContract):
    """Display-only values. Never nest this object in SpaceComparisonInput."""

    scene_id: str
    dong: str | None
    weather: dict[str, JsonValue] | None
