"""Typed narrative inputs. Audit bindings are internal; prose is never a fact."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from daengs_walk.diary.contracts.input import Anchor, RecordRef
from daengs_walk.diary.relational.contracts import CurrentMotion
from daengs_walk.diary.relational.scene_comparison_contracts import FactScope, SceneConnection
from daengs_walk.diary.relational.walk_phase import ScenePosition
from daengs_walk.value_contracts import Instant, Point, ValueContract, digest

PROJECTION_VERSION = "narrative-space-v1"


class RoadMeaning(ValueContract):
    kind: Literal["road"] = "road"
    name: str = Field(min_length=1)


class CoverMeaning(ValueContract):
    kind: Literal["land_cover"] = "land_cover"
    label: str = Field(min_length=1)
    classification: str | None = None


class ObjectMeaning(ValueContract):
    kind: Literal["surrounding_object"] = "surrounding_object"
    name: str = Field(min_length=1)
    park_type: str | None = None
    distance_m: float = Field(ge=0)
    area_m2: float | None = Field(default=None, ge=0)


class AreaMeaning(ValueContract):
    kind: Literal["area_context"] = "area_context"
    business_mix: str | None = None
    spatial_distribution: str | None = None
    radius_m: float = Field(gt=0)

    @model_validator(mode="after")
    def has_characteristics(self):
        if not self.business_mix and not self.spatial_distribution:
            raise ValueError("area requires at least one normalized characteristic")
        return self


SpatialMeaning = Annotated[
    RoadMeaning | CoverMeaning | ObjectMeaning | AreaMeaning, Field(discriminator="kind")
]


class NarrativeFact(ValueContract):
    id: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    meaning: SpatialMeaning
    scope: FactScope
    observed_at: Instant | None = None
    reference_date: str | None = None
    time_meaning: str = Field(min_length=1)

    @model_validator(mode="after")
    def scope_matches_meaning(self):
        expected = {
            "road": "record_point",
            "land_cover": "record_point",
            "surrounding_object": "registered_point",
            "area_context": "query_area",
        }
        if self.scope.kind != expected[self.meaning.kind]:
            raise ValueError("spatial meaning has the wrong scope")
        return self


class NarrativeRelation(ValueContract):
    id: str = Field(min_length=1)
    family: Literal["road", "land_cover", "surrounding_object", "area_context"]
    axis: Literal["record_location", "object_distance", "query_area"]
    result: Literal[
        "same_characteristics", "different_characteristics", "nearer", "farther", "same_distance"
    ]
    earlier_evidence_ids: tuple[str, ...] = Field(min_length=1)
    current_evidence_ids: tuple[str, ...] = Field(min_length=1)
    scope: str = Field(min_length=1)
    establishes_temporal_change: Literal[False] = False

    @model_validator(mode="after")
    def relationship_kind(self):
        axis = {
            "road": "record_location",
            "land_cover": "record_location",
            "surrounding_object": "object_distance",
            "area_context": "query_area",
        }[self.family]
        results = (
            {"nearer", "farther", "same_distance"}
            if self.family == "surrounding_object"
            else {"same_characteristics", "different_characteristics"}
        )
        if self.axis != axis or self.result not in results:
            raise ValueError("narrative relation family/axis/result disagree")
        return self


class NarrativeRelationSlots(ValueContract):
    background: tuple[NarrativeRelation, ...] = ()
    proximity: tuple[NarrativeRelation, ...] = ()
    area_context: tuple[NarrativeRelation, ...] = ()

    def all_relations(self):
        return self.background + self.proximity + self.area_context

    @model_validator(mode="after")
    def families(self):
        for items, allowed in (
            (self.background, {"road", "land_cover"}),
            (self.proximity, {"surrounding_object"}),
            (self.area_context, {"area_context"}),
        ):
            if any(r.family not in allowed for r in items):
                raise ValueError("narrative relation in wrong slot")
        return self


class SpatialAnchor(ValueContract):
    position: ScenePosition
    point: Point | None
    position_basis: Literal["observed", "estimated", "last_known", "none"]
    accuracy_m: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def located(self):
        if (self.point is None) != (self.position_basis == "none"):
            raise ValueError("spatial anchor position basis mismatch")
        return self


class TimeGap(ValueContract):
    start: Instant
    end: Instant

    @model_validator(mode="after")
    def ordered(self):
        if self.start >= self.end:
            raise ValueError("empty/reversed observation gap")
        return self


class RouteInterval(ValueContract):
    id: str
    status: Literal["connected", "partial"]
    started_at: Instant
    ended_at: Instant
    elapsed_seconds: float = Field(gt=0)
    observed_seconds: float = Field(ge=0)
    observed_distance_m: float = Field(ge=0)
    moving_distance_m: float = Field(ge=0)
    uncovered_intervals: tuple[TimeGap, ...]
    subject: Literal["recording_device"] = "recording_device"
    space_scope: Literal["endpoint_background_only"] = "endpoint_background_only"

    @model_validator(mode="after")
    def coverage(self):
        if (
            abs((self.ended_at - self.started_at).total_seconds() - self.elapsed_seconds) > 1e-6
            or self.observed_seconds > self.elapsed_seconds + 1e-6
            or self.moving_distance_m > self.observed_distance_m
        ):
            raise ValueError("route metrics disagree with observed interval")
        if self.status == "connected" and (
            self.uncovered_intervals or abs(self.observed_seconds - self.elapsed_seconds) > 1e-6
        ):
            raise ValueError("connected route cannot have an observation gap")
        cursor = self.started_at
        for gap in self.uncovered_intervals:
            if gap.start < cursor or gap.end > self.ended_at:
                raise ValueError("route gaps overlap or exceed interval")
            cursor = gap.end
        return self


class NarrativeSpaceContext(ValueContract):
    version: Literal["narrative-space-v1"] = PROJECTION_VERSION
    current: SpatialAnchor
    earlier: SpatialAnchor | None
    facts: tuple[NarrativeFact, ...]
    relation_slots: NarrativeRelationSlots
    connection: SceneConnection | None = None
    route: RouteInterval | None = None
    # Internal source references/omissions must not enter the writer projection.
    source_bindings: dict[str, tuple[str, ...]]
    object_identities: dict[str, str] = Field(default_factory=dict)
    omitted_sources: dict[str, str] = Field(default_factory=dict)

    @property
    def current_facts(self):
        return tuple(f for f in self.facts if f.scene_id == self.current.position.scene_id)

    @property
    def signature(self):
        # Shared by planning and delivery; no counts, source dates, or acquisition IDs.
        return self.signature_for(self.current.position.scene_id)

    def signature_for(self, scene_id):
        meanings = {}
        for fact in self.facts:
            if fact.scene_id != scene_id:
                continue
            meaning = [
                fact.meaning.model_dump(mode="json"),
                fact.scope.kind,
                self.object_identities.get(fact.id),
            ]
            meanings[digest(meaning)] = meaning
        return digest([self.version, sorted(meanings.values(), key=digest)]) if meanings else None

    @model_validator(mode="after")
    def references(self):
        current = self.current.position
        earlier = self.earlier.position if self.earlier else None
        if earlier:
            if (
                earlier.timeline != current.timeline
                or earlier.scene_id == current.scene_id
                or earlier.selected_scene_count != current.selected_scene_count
                or earlier.selected_scene_number >= current.selected_scene_number
                or earlier.recorded_at > current.recorded_at
            ):
                raise ValueError("space context needs ordered scenes from the same walk")
            c = self.connection
            if c is None or (c.earlier_scene_id, c.current_scene_id) != (
                earlier.scene_id,
                current.scene_id,
            ):
                raise ValueError("connection endpoints differ from spatial anchors")
            if c.elapsed_seconds != (current.recorded_at - earlier.recorded_at).total_seconds():
                raise ValueError("connection elapsed time differs from anchors")
        elif self.connection or self.relation_slots.all_relations():
            raise ValueError("initial context cannot contain a comparison")
        facts = {f.id: f for f in self.facts}
        relations = self.relation_slots.all_relations()
        ids = (
            [f.id for f in self.facts]
            + [r.id for r in relations]
            + ([self.route.id] if self.route else [])
        )
        route_ids = self.connection.route_evidence_ids if self.connection else ()
        if route_ids != ((self.route.id,) if self.route else ()):
            raise ValueError("route reference must resolve in context")
        if self.route and (
            earlier is None
            or self.route.started_at != earlier.recorded_at
            or self.route.ended_at != current.recorded_at
            or self.route.status != self.connection.route_status
        ):
            raise ValueError("route belongs to a different scene interval")
        if len(ids) != len(set(ids)) or set(ids) != self.source_bindings.keys():
            raise ValueError("narrative IDs and source bindings must agree uniquely")
        if any(not refs for refs in self.source_bindings.values()):
            raise ValueError("narrative meaning has no source binding")
        object_ids = {f.id for f in self.facts if f.meaning.kind == "surrounding_object"}
        if self.object_identities.keys() != object_ids or any(
            not v for v in self.object_identities.values()
        ):
            raise ValueError("object identity bindings do not match surrounding facts")
        scenes = {current.scene_id} | ({earlier.scene_id} if earlier else set())
        if any(f.scene_id not in scenes for f in self.facts):
            raise ValueError("fact belongs to a different scene")
        for r in relations:
            for selected, scene in (
                (r.earlier_evidence_ids, earlier),
                (r.current_evidence_ids, current),
            ):
                if scene is None or any(
                    i not in facts
                    or facts[i].scene_id != scene.scene_id
                    or facts[i].meaning.kind != r.family
                    for i in selected
                ):
                    raise ValueError("relation endpoint or family mismatch")
        return self


class DogActor(ValueContract):
    entity_type: Literal["dog"] = "dog"
    pet_id: str | None = None
    name: str | None = None

    @model_validator(mode="after")
    def named_identity(self):
        if self.pet_id is None and self.name is not None:
            raise ValueError("unspecified dog cannot receive an inferred name")
        return self


class CurrentDogEvent(ValueContract):
    id: str = Field(min_length=1)
    source_record: RecordRef
    actor: DogActor
    behavior: Literal["sniffing", "excretion", "barking"]
    anchor: Anchor
    scene_id: str = Field(min_length=1)


class EventContext(ValueContract):
    for_event_id: str
    evidence: NarrativeFact | CurrentMotion
    kind: Literal["space", "current_gait", "current_shape"]
    subject: Literal["record_location", "recording_device"]

    @model_validator(mode="after")
    def current_only(self):
        spatial = isinstance(self.evidence, NarrativeFact)
        if (self.kind == "space") != spatial or self.subject != (
            "record_location" if spatial else "recording_device"
        ):
            raise ValueError("event context kind and subject mismatch")
        if spatial and self.evidence.meaning.kind not in {"road", "land_cover"}:
            raise ValueError("action context only accepts current point background")
        return self


class ActionWritingBrief(ValueContract):
    version: Literal["action-writing-brief-v1"] = "action-writing-brief-v1"
    part: Literal["action"] = "action"
    position: ScenePosition
    required_event: CurrentDogEvent
    context_options: tuple[EventContext, ...] = ()

    @property
    def citation_ids(self):
        return (self.required_event.id, *(c.evidence.id for c in self.context_options))

    @model_validator(mode="after")
    def event_binding(self):
        event = self.required_event
        if (
            event.scene_id != self.position.scene_id
            or event.anchor.event_at != self.position.recorded_at
        ):
            raise ValueError("event must belong to the current scene/time")
        if len(self.citation_ids) != len(set(self.citation_ids)):
            raise ValueError("duplicate event/context citation")
        for item in self.context_options:
            if item.for_event_id != event.id:
                raise ValueError("context is bound to a different event")
            if (
                isinstance(item.evidence, NarrativeFact)
                and item.evidence.scene_id != event.scene_id
            ):
                raise ValueError("past or future space cannot enter current action")
        return self


class DeliveredMeaning(ValueContract):
    """Only accepted citation selections, not the prior generated sentence or action."""

    context: NarrativeSpaceContext
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    relation_ids: tuple[str, ...] = ()
    semantic_status: Literal["unverified", "model_reviewed"] = "unverified"

    @model_validator(mode="after")
    def known_selection(self):
        evidence = {f.id for f in self.context.facts}
        if self.context.route:
            evidence.add(self.context.route.id)
        relations = {r.id for r in self.context.relation_slots.all_relations()}
        if not set(self.evidence_ids) <= evidence or not set(self.relation_ids) <= relations:
            raise ValueError("delivery cites unknown narrative meaning")
        if len(set(self.evidence_ids)) != len(self.evidence_ids) or len(
            set(self.relation_ids)
        ) != len(self.relation_ids):
            raise ValueError("duplicate delivery citation")
        return self


class BriefDeliveryState(ValueContract):
    version: Literal["brief-delivery-v1"] = "brief-delivery-v1"
    active_signature: str | None = None
    recent: tuple[DeliveredMeaning, ...] = Field(default=(), max_length=2)
    all_context_facts_delivered: Literal[False] = False


class SpaceWritingBrief(ValueContract):
    version: Literal["space-writing-brief-v1"] = "space-writing-brief-v1"
    part: Literal["space"] = "space"
    context: NarrativeSpaceContext
    delivery: BriefDeliveryState = Field(default_factory=BriefDeliveryState)

    @property
    def citation_ids(self):
        return tuple(f.id for f in self.context.facts) + (
            (self.context.route.id,) if self.context.route else ()
        )

    @property
    def relation_ids(self):
        return tuple(r.id for r in self.context.relation_slots.all_relations())

    @model_validator(mode="after")
    def memory_is_prior_space(self):
        position = self.context.current.position
        for memory in self.delivery.recent:
            earlier = memory.context.current.position
            if (
                earlier.timeline != position.timeline
                or earlier.recorded_at > position.recorded_at
                or earlier.selected_scene_number >= position.selected_scene_number
            ):
                raise ValueError("memory must belong to earlier scenes of this walk")
        return self
