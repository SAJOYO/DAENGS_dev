"""Shared mechanical correspondence contracts; no narrative ranking or sentences."""

from daengs_walk.diary.relational.scene_comparison_contracts import (
    SpatialComparisonSlot,
    SpatialCorrespondence,
)
from daengs_walk.value_contracts import digest


def facts(scene, family):
    return sorted((f for f in scene.facts if f.family == family), key=lambda f: f.id)


def relation(family, left, right, result, scope, **details):
    data = dict(
        family=family,
        axis={
            "road": "record_location",
            "land_cover": "record_location",
            "surrounding_object": "object_distance",
            "area_context": "query_area",
        }[family],
        result=result,
        earlier_evidence_ids=tuple(sorted(f.id for f in left)),
        current_evidence_ids=tuple(sorted(f.id for f in right)),
        scope=scope,
        **details,
    )
    return SpatialCorrespondence(id="comparison:" + digest(data), **data)


def one_sided(family, left, right):
    return relation(
        family,
        left,
        right,
        "only_one_snapshot_has_evidence",
        "한쪽 스냅샷에만 근거가 있음. 대상의 출현·소멸·이탈은 확정하지 않음",
    )


def result_slot(items, *, initial=False):
    if initial:
        return SpatialComparisonSlot(status="not_applicable", reason="initial_scene")
    uncertain = any(
        r.result in {"incomparable", "only_one_snapshot_has_evidence"}
        or r.comparison_basis.get("statistics_comparable") is False
        for r in items
    )
    changed = any(
        r.result in {"different_values", "different_query_areas"}
        or r.distance_delta_m not in (None, 0)
        or r.comparison_basis.get("changed_fields")
        for r in items
    )
    status = (
        "insufficient_evidence"
        if uncertain or not items
        else "confirmed"
        if changed
        else "no_change"
    )
    return SpatialComparisonSlot(
        status=status,
        reason="see_individual_correspondences" if items else "no_comparable_facts",
        items=tuple(items),
    )


def unique_value(items, extract):
    values = {digest(extract(f)): extract(f) for f in items}
    return next(iter(values.values())) if len(values) == 1 else None
