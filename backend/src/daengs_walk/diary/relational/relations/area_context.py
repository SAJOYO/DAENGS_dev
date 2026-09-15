"""Query-footprint and composition comparison; never infer a street scene."""

from .correspondence import facts, one_sided, relation, result_slot

ATTRIBUTES = (
    "composition",
    "groups",
    "registered_count",
    "registered_sites",
    "spread_rms_m",
    "nearest_registered_point_m",
    "centroid_distance_m",
    "beverage_count",
)


def evaluate_snapshot(current, earlier):
    if earlier is None:
        return result_slot([], initial=True)
    left, right = facts(earlier, "area_context"), facts(current, "area_context")
    if not left and not right:
        return result_slot([])
    if not left or not right:
        return result_slot([one_sided("area_context", left, right)])
    scope = (
        "조회 영역 전체의 등록 자료 비교. 지점의 풍경·혼잡·동일 지역의 시간 변화는 확정하지 않음"
    )
    if len(left) != 1 or len(right) != 1:
        return result_slot(
            [
                relation(
                    "area_context",
                    left,
                    right,
                    "incomparable",
                    scope,
                    comparison_basis={"reason": "ambiguous_area_pairing"},
                )
            ]
        )
    a, b = left[0], right[0]
    av, bv = a.value, b.value
    queries = [v.get("query") for v in (av, bv)]
    if not all(
        isinstance(q, dict)
        and q.get("kind") == "query_circle"
        and q.get("point") is not None
        and q.get("radius_m") is not None
        for q in queries
    ):
        return result_slot(
            [
                relation(
                    "area_context",
                    left,
                    right,
                    "incomparable",
                    scope,
                    comparison_basis={"reason": "missing_query_geometry"},
                )
            ]
        )
    comparable = (
        a.subject_key is not None
        and b.subject_key is not None
        and a.subject_key.split(":area:")[0] == b.subject_key.split(":area:")[0]
        and a.reference_date is not None
        and a.reference_date == b.reference_date
        and queries[0]["radius_m"] == queries[1]["radius_m"]
        and av.get("classification_policy") is not None
        and av.get("classification_policy") == bv.get("classification_policy")
        and av.get("composition") is not None
        and bv.get("composition") is not None
    )
    changed = (
        [k for k in ATTRIBUTES if k in av and k in bv and av[k] != bv[k]] if comparable else []
    )
    return result_slot(
        [
            relation(
                "area_context",
                left,
                right,
                "same_query_area" if queries[0] == queries[1] else "different_query_areas",
                scope,
                composition_values_equal=av["composition"] == bv["composition"]
                if comparable
                else None,
                comparison_basis={
                    "statistics_comparable": comparable,
                    "changed_fields": changed,
                    "reason": "same_series_month_radius_policy"
                    if comparable
                    else "statistics_basis_missing_or_different",
                },
            )
        ]
    )
