"""Point comparisons and supported device intervals remain separate evidence."""

from daengs_walk.diary.relational.comparison import compare_basis
from daengs_walk.diary.relational.contracts import RecordPointObservation, SpatialRelation


def record_point(frame, material):
    return RecordPointObservation(
        record_point_id=frame["scene_id"],
        recorded_at=frame["anchor"]["event_at"],
        coordinate=frame["anchor"].get("point"),
        observation=material,
    )


def spatial_context(frame):
    result = {
        m["role"]: m
        for m in frame["space"].get("materials", [])
        if m["role"] in {"point_land_cover", "location_label"}
    }
    if frame.get("road_reference"):
        road = frame["road_reference"]
        result["road_address"] = {
            "id": road["id"],
            "role": "road_address",
            "material": {"road_nm": road["road_nm"]},
            "relation": road["scope"],
        }
    return result


def spatial_relations(frame, previous):
    current = spatial_context(frame)
    prior = spatial_context(previous) if previous else {}
    result = []
    for role in sorted(current.keys() | prior.keys()):
        before, after = prior.get(role), current.get(role)
        kind = (
            "unconfirmed"
            if after is None
            else "introduced"
            if before is None
            else "matching_points"
            if before["material"] == after["material"]
            else "deferred"
        )
        axis, basis = None, {}
        if kind == "deferred":
            axis, basis = compare_basis(previous, frame, role)
            if axis:
                kind = {
                    "location": "location_contrast",
                    "record_location": "record_context_contrast",
                    "observation_time": "temporal_contrast",
                }[axis]
        sources = []
        if before:
            sources.append(previous["scene_id"] + ":" + before["id"])
        if after:
            sources.append(frame["scene_id"] + ":" + after["id"])
        result.append(
            SpatialRelation(
                id=f"r{len(result) + 1}",
                kind=kind,
                role=role,
                comparison_axis=axis,
                comparison_basis=basis,
                earlier_record_point=record_point(previous, before) if previous else None,
                current_record_point=record_point(frame, after),
                source_ids=tuple(sources),
            ).model_dump(mode="json")
        )
    return result


def evaluate(frame, previous):
    from .contracts import slot

    relations = spatial_relations(frame, previous)
    kinds = {r["kind"] for r in relations}
    if not kinds or kinds <= {"unconfirmed", "deferred"}:
        status = "insufficient_evidence"
    elif kinds <= {"matching_points"}:
        status = "no_change"
    else:
        status = "confirmed"
    return slot(
        status, "Point background comparison; each item retains its own determination", relations
    )
