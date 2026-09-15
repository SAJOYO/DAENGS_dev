"""One semantic projection for planning, delivery signatures and writer inputs."""

from daengs_walk.diary.relational.brief_contracts import (
    PROJECTION_VERSION,
    AreaMeaning,
    CoverMeaning,
    NarrativeFact,
    NarrativeRelation,
    NarrativeRelationSlots,
    NarrativeSpaceContext,
    ObjectMeaning,
    RoadMeaning,
    RouteInterval,
    SpatialAnchor,
)
from daengs_walk.diary.relational.relations.registry import collect_spatial_comparisons
from daengs_walk.diary.relational.scene_comparison_contracts import FactScope, SpaceComparisonInput
from daengs_walk.diary.space.road import road_name
from daengs_walk.value_contracts import digest


def project_fact(fact, scene_id):
    """Missing supported content is explicit. Raw acquisition values never become prose."""
    value = fact.value
    if fact.family == "road":
        name = road_name(value.get("name"))
        if not name:
            return None
        meaning = RoadMeaning(name=name)
    elif fact.family == "land_cover":
        label = value.get("피복")
        if not isinstance(label, str) or not label.strip():
            return None
        classification = value.get("classification") or {}
        meaning = CoverMeaning(label=label, classification=classification.get("피복"))
    elif fact.family == "surrounding_object":
        if not value.get("name") or value.get("distance_m") is None:
            return None
        meaning = ObjectMeaning(
            **{k: value[k] for k in ("name", "park_type", "distance_m", "area_m2") if k in value}
        )
    else:
        composition, query = value.get("composition"), value.get("query")
        if not isinstance(composition, dict) or not isinstance(query, dict):
            return None
        mix, distribution = composition.get("업종구성"), composition.get("조회영역_등록분포")
        if not (mix or distribution) or query.get("radius_m") is None:
            return None
        meaning = AreaMeaning(
            business_mix=mix,
            spatial_distribution=distribution,
            radius_m=query["radius_m"],
        )
    # coverage_key describes acquisition identity, not a narrated property.
    scope = FactScope(kind=fact.scope.kind, description=fact.scope.description)
    data = {
        "scene_id": scene_id,
        "meaning": meaning.model_dump(mode="json"),
        "scope": scope.model_dump(mode="json"),
        "observed_at": fact.observed_at,
        "reference_date": fact.reference_date,
        "time_meaning": fact.time_meaning,
    }
    identity = digest([PROJECTION_VERSION, fact.id, meaning.model_dump(mode="json")])
    return NarrativeFact(id="nf:" + identity, **data)


def build_space_context(request, positions):
    request = SpaceComparisonInput.model_validate(request)
    rebuilt = collect_spatial_comparisons(request.current, request.earlier)
    if rebuilt != request.relation_slots.model_dump(mode="json"):
        raise ValueError("spatial comparisons do not match their source snapshots")
    facts, by_source, bindings, omitted, identities = [], {}, {}, {}, {}

    def anchor(scene):
        position = positions[scene.scene_id]
        if (
            position.scene_id != scene.scene_id
            or position.recorded_at != scene.recorded_at
            or position.timeline.source_revision != scene.walk_id
        ):
            raise ValueError("scene position does not match source snapshot")
        return SpatialAnchor(
            position=position,
            point=scene.point,
            position_basis=scene.position_basis,
            accuracy_m=scene.accuracy_m,
        )

    for scene in (request.earlier, request.current):
        if scene is None:
            continue
        for raw in scene.facts:
            fact = project_fact(raw, scene.scene_id)
            if fact is None:
                omitted[raw.id] = "missing_supported_spatial_meaning"
                continue
            facts.append(fact)
            by_source[raw.id] = fact
            bindings[fact.id] = (raw.id, *raw.source_refs)
            if raw.family == "surrounding_object":
                identities[fact.id] = raw.subject_key or "unresolved:" + raw.id
    slots = {"background": [], "proximity": [], "area_context": []}
    for raw in request.relation_slots.all_relations():
        ids = raw.earlier_evidence_ids + raw.current_evidence_ids
        if raw.result in {"incomparable", "only_one_snapshot_has_evidence"} or any(
            i not in by_source for i in ids
        ):
            omitted[raw.id] = "no_supported_two_endpoint_relation"
            continue
        left = [by_source[i] for i in raw.earlier_evidence_ids]
        right = [by_source[i] for i in raw.current_evidence_ids]
        if raw.family == "area_context":
            if not raw.comparison_basis.get("statistics_comparable"):
                omitted[raw.id] = "area_series_or_scope_not_comparable"
                continue
            # Missing a characteristic at one end is not a change of that characteristic.
            fields = [set(f.meaning.model_dump(exclude_none=True)) for f in left + right]
            if any(keys != fields[0] for keys in fields[1:]):
                omitted[raw.id] = "area_characteristics_incomplete"
                continue
        if raw.family == "surrounding_object":
            if raw.distance_delta_m is None:
                omitted[raw.id] = "distance_comparison_unavailable"
                continue
            result = (
                "nearer"
                if raw.distance_delta_m < 0
                else "farther"
                if raw.distance_delta_m > 0
                else "same_distance"
            )
            slot = "proximity"
        else:
            a = {digest(f.meaning) for f in left}
            b = {digest(f.meaning) for f in right}
            result = "same_characteristics" if a == b else "different_characteristics"
            slot = "area_context" if raw.family == "area_context" else "background"
        relation = NarrativeRelation(
            id="nr:" + digest([PROJECTION_VERSION, raw.id, result, [f.id for f in left + right]]),
            family=raw.family,
            axis=raw.axis,
            result=result,
            earlier_evidence_ids=tuple(f.id for f in left),
            current_evidence_ids=tuple(f.id for f in right),
            scope=raw.scope,
        )
        slots[slot].append(relation)
        bindings[relation.id] = (raw.id,)
    route = None
    if request.route_evidence:
        raw = request.route_evidence
        route = RouteInterval(**{k: raw[k] for k in RouteInterval.model_fields if k in raw})
        bindings[route.id] = (raw["id"],)
    return NarrativeSpaceContext(
        current=anchor(request.current),
        earlier=anchor(request.earlier) if request.earlier else None,
        facts=tuple(facts),
        relation_slots=NarrativeRelationSlots(**slots),
        connection=request.connection,
        route=route,
        source_bindings=bindings,
        object_identities=identities,
        omitted_sources=omitted,
    )
