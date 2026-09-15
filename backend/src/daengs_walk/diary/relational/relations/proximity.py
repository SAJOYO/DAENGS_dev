"""Same-object endpoint distances. Legacy frames remain explicitly unsupported."""

from math import isfinite

from .contracts import slot
from .correspondence import facts, one_sided, relation, result_slot, unique_value


def evaluate(frame, previous):
    return slot("not_implemented", "Same-object distance observations are not yet bound to frames")


def evaluate_snapshot(current, earlier):
    if earlier is None:
        return result_slot([], initial=True)
    groups = []
    for scene in (earlier, current):
        grouped = {}
        for fact in facts(scene, "surrounding_object"):
            # Missing identity never falls back to a name match.
            key = ("object", fact.subject_key) if fact.subject_key else ("unidentified", fact.id)
            grouped.setdefault(key, []).append(fact)
        groups.append(grouped)
    items = []
    for key in sorted(groups[0].keys() | groups[1].keys()):
        left, right = (group.get(key, []) for group in groups)
        if not left or not right:
            items.append(one_sided("surrounding_object", left, right))
            continue
        points = [
            unique_value(side, lambda f: f.value.get("registered_point")) for side in (left, right)
        ]
        distances = [
            unique_value(side, lambda f: f.value.get("distance_m")) for side in (left, right)
        ]
        comparable = (
            points[0] is not None
            and points[0] == points[1]
            and all(type(d) in (int, float) and isfinite(d) and d >= 0 for d in distances)
        )
        items.append(
            relation(
                "surrounding_object",
                left,
                right,
                "same_object" if comparable else "incomparable",
                "동일 공원 등록점까지의 양 끝 거리 차이. 중간의 지속적 접근·공원 진입은 미확인",
                distance_delta_m=round(distances[1] - distances[0], 3) if comparable else None,
                comparison_basis={
                    "subject_key": key[1],
                    "registered_point_equal": points[0] == points[1],
                    "reason": "endpoint_distances"
                    if comparable
                    else "changed_registration_or_invalid_distances",
                },
            )
        )
    return result_slot(items)
