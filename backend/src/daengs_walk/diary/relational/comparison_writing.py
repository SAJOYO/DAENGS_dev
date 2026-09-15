"""Build the whole-scene writing task from verified snapshots and computed slots."""

from copy import deepcopy

from daengs_walk.diary.relational.scene_comparison_contracts import (
    SceneConnection,
    SceneSnapshot,
    SpaceComparisonInput,
    SpatialComparisonSlots,
)
from daengs_walk.value_contracts import digest


def comparison_input(frame, previous=None):
    current = SceneSnapshot.model_validate(frame["scene_snapshot"])
    earlier = SceneSnapshot.model_validate(previous["scene_snapshot"]) if previous else None
    connection, route = None, None
    if earlier:
        journey = frame.get("journey")
        # Retain partial coverage explicitly; it never becomes a connected route.
        if journey and journey.get("segments"):
            route = {"id": "journey:" + digest(journey), **deepcopy(journey)}
        connection = SceneConnection(
            earlier_scene_id=earlier.scene_id,
            current_scene_id=current.scene_id,
            elapsed_seconds=(current.recorded_at - earlier.recorded_at).total_seconds(),
            route_status=journey["status"] if route else "unavailable",
            route_evidence_ids=(route["id"],) if route else (),
            scope="두 기록 사이의 경과 시간. 이동을 설명할 범위는 route_status와 관측 근거를 따른다",
        )
    return SpaceComparisonInput(
        current=current,
        earlier=earlier,
        connection=connection,
        relation_slots=frame["spatial_comparison_slots"],
        route_evidence=route,
        narration=frame["space"].get("narration"),
    )


def should_write_space(frame, previous=None):
    if not frame["scene_snapshot"]["facts"]:
        return False
    if previous is None:
        return True
    slots = SpatialComparisonSlots.model_validate(frame["spatial_comparison_slots"])
    # No material hierarchy; mixed slots can contain both usable and unknown items.
    return any(
        r.result in {"different_values", "different_query_areas"}
        or r.distance_delta_m not in (None, 0)
        or r.comparison_basis.get("changed_fields")
        or (r.result == "only_one_snapshot_has_evidence" and r.current_evidence_ids)
        for r in slots.all_relations()
    )


def writer_projection(payload):
    """Keep all semantic facts while separating audit IDs from writer citations."""
    request = SpaceComparisonInput.model_validate(payload)
    data = request.model_dump(mode="json")
    evidence_map, relation_map = citation_maps(payload)
    evidence_alias = {v: k for k, v in evidence_map.items()}
    relation_alias = {v: k for k, v in relation_map.items()}
    for side in ("earlier", "current"):
        if data[side]:
            for fact in data[side]["facts"]:
                fact["id"] = evidence_alias[fact["id"]]
                fact.pop("source_refs")
                fact.pop("retrieved_at")
    if data["route_evidence"]:
        data["route_evidence"]["id"] = evidence_alias[data["route_evidence"]["id"]]
        data["connection"]["route_evidence_ids"] = [
            evidence_alias[k] for k in data["connection"]["route_evidence_ids"]
        ]
        for key in ("source_revision", "segments", "endpoint_binding"):
            data["route_evidence"].pop(key, None)
    for slot in data["relation_slots"].values():
        for relation in slot["items"]:
            relation["id"] = relation_alias[relation["id"]]
            for key in ("earlier_evidence_ids", "current_evidence_ids"):
                relation[key] = [evidence_alias[k] for k in relation[key]]
    data["citation_ids"] = list(evidence_map)
    data["relation_ids"] = [
        relation_alias[r.id]
        for r in request.relation_slots.all_relations()
        if r.result not in {"incomparable", "only_one_snapshot_has_evidence"}
    ]
    return data


def citation_maps(payload):
    request = SpaceComparisonInput.model_validate(payload)
    return (
        {f"e{i}": key for i, key in enumerate(request.citation_ids, 1)},
        {f"r{i}": r.id for i, r in enumerate(request.relation_slots.all_relations(), 1)},
    )


def resolve_answer(payload, answer):
    from daengs_walk.diary.relational.scene_comparison_contracts import SpaceComparisonAnswer

    evidence, relations = citation_maps(payload)
    visible = writer_projection(payload)
    if not set(answer["relation_ids"]) <= set(visible["relation_ids"]):
        raise ValueError("unknown or unusable selected comparison relation")
    resolved = {
        **answer,
        "evidence_ids": [evidence[k] for k in answer["evidence_ids"]],
        "relation_ids": [relations[k] for k in answer["relation_ids"]],
    }
    return SpaceComparisonAnswer.model_validate(resolved).validate_against(
        SpaceComparisonInput.model_validate(payload)
    )
