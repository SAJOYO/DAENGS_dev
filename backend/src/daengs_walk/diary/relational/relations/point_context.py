"""Compare address names and normalized point cover, excluding metadata churn."""

from .correspondence import facts, one_sided, relation, result_slot, unique_value


def evaluate_snapshot(current, earlier):
    if earlier is None:
        return result_slot([], initial=True)
    items = []
    for family, attribute in (("road", "name"), ("land_cover", "classification")):
        left, right = facts(earlier, family), facts(current, family)
        if not left and not right:
            continue
        if not left or not right:
            items.append(one_sided(family, left, right))
            continue
        a, b = (
            unique_value(side, lambda f, key=attribute: f.value.get(key)) for side in (left, right)
        )
        known_dates = {f.reference_date for f in left + right if f.reference_date is not None}
        layers = {f.value.get("layer") for f in left + right}
        comparable = a is not None and b is not None and len(layers) == 1
        items.append(
            relation(
                family,
                left,
                right,
                ("same_value" if a == b else "different_values") if comparable else "incomparable",
                "두 기록 위치에 연결된 주소·지도 분류의 비교. 동일 장소의 시간 변화·중간 통과는 미확인",
                comparison_basis={
                    "attribute": attribute,
                    "source_dates": sorted(known_dates),
                    "source_dates_differ": len(known_dates) > 1,
                    "source_time_unknown": any(f.reference_date is None for f in left + right),
                    "reason": "normalized_values"
                    if comparable
                    else "conflicting_values_or_source_versions",
                },
            )
        )
    return result_slot(items)
