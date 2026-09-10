"""Render only server-owned scope, source facts and recorded selection provenance."""

from daengs_place.place.conversation.contract import AnswerFact

KINDS = {
    "hospital": "동물병원",
    "pharmacy": "동물약국",
    "pet_shop": "반려동물용품점",
    "shopping": "쇼핑",
    "grooming": "미용",
    "boarding": "위탁관리",
    "travel": "여행지",
    "leisure": "레저",
    "museum": "박물관",
    "gallery": "미술관",
    "arts_center": "문예회관",
    "culture": "문화시설",
    "cafe": "카페",
    "restaurant": "음식점",
    "pension": "펜션",
    "hotel": "호텔",
    "stay": "숙박",
    "etc": "기타",
}
ATTRIBUTES = {
    "parking": "주차",
    "exclusive": "반려동물 전용 여부",
    "pet_allowed": "반려동물 동반 가능 여부",
    "quiet": "조용함",
    "free": "무료 여부",
    "other": "요청한 추가 속성",
    "distance": "거리",
    "address": "주소",
    "selection_reason": "선택 이유",
}


def describe_filters(filters):
    def condition(a):
        if a.capability == "purpose.kind":
            names = "/".join(KINDS.get(k, k) for k in a.value)
            return names if a.op == "in" else f"{names} 제외"
        if a.capability == "operations.parking":
            return "주차 가능" if a.value else "주차 불가"
        return "반려동물 전용" if a.value else "반려동물 전용 아님"

    parts = ["/".join(KINDS.get(k, k) for k in filters.candidate_kinds)]
    parts.extend(condition(a) for a in filters.hard.all)
    if filters.hard.any:
        parts.append(
            " 또는 ".join(
                "(" + " · ".join(condition(a) for a in b.all) + ")" for b in filters.hard.any
            )
        )
    for preference in filters.preferences:
        kinds = "/".join(KINDS.get(k, k) for k in preference.scope_kinds)
        parts.append(f"{kinds}는 주차 가능 우선")
    if filters.name_query:
        parts.append(f"이름에 ‘{filters.name_query}’ 포함")
    parts.append(f"현재 검색 중심에서 {filters.spatial.radius_m}m 이내")
    return ", ".join(parts)


def confirmation(candidate, unsupported, goal):
    missing = "·".join(ATTRIBUTES[a] for a in dict.fromkeys(unsupported))
    action = "조건만 바꿀까요?" if goal == "edit_only" else "찾아볼까요?"
    return (
        f"{missing} 조건은 검색에 적용할 수 없어요. {describe_filters(candidate)} 조건으로 {action}"
    )


def selected_facts(place, attributes):
    facts = []
    for attribute in dict.fromkeys(attributes):
        if attribute in {"quiet", "free", "other"}:
            facts.append(AnswerFact(attribute=attribute, status="unsupported"))
            continue
        if attribute == "selection_reason":
            continue  # A place fact is never evidence of why it was chosen.
        path = {
            "parking": "facts.parking",
            "exclusive": "facts.pet_access.exclusive",
            "pet_allowed": "facts.pet_access.allowed",
            "address": "facts.address",
            "distance": "distance_m",
        }[attribute]
        value = place
        for part in path.split("."):
            value = getattr(value, part, None)
        source = place.field_sources.get(path)
        classification = next(c for c in place.classifications if c.source == place.key)
        facts.append(
            AnswerFact(
                attribute=attribute,
                status="unknown" if value is None else "known",
                value=value,
                source=source.source if source else place.key,
                as_of=source.as_of if source else classification.as_of,
            )
        )
    return tuple(facts)


def fact_sentence(fact):
    label = ATTRIBUTES[fact.attribute]
    if fact.status == "unsupported":
        return f"{label}에 관한 정보는 현재 제공되지 않아 확인할 수 없어요."
    if fact.status == "unknown":
        return f"{label} 정보는 없어서 확인이 필요해요."
    if fact.attribute == "parking":
        return (
            "원천 정보에는 주차 가능으로 나와요."
            if fact.value
            else "원천 정보에는 주차 불가로 나와요."
        )
    if fact.attribute == "exclusive":
        return (
            "원천 정보에는 반려동물 전용으로 나와요."
            if fact.value
            else "원천 정보에는 반려동물 전용이 아닌 것으로 나와요."
        )
    if fact.attribute == "pet_allowed":
        return (
            "원천 정보에는 반려동물 동반 가능으로 나와요."
            if fact.value
            else "원천 정보에는 반려동물 동반 불가로 나와요."
        )
    if fact.attribute == "distance":
        return f"검색 중심에서 {fact.value}m 거리예요."
    return f"주소는 {fact.value}예요."


def render_answer(receipt):
    if receipt.execution == "failed":
        return "검색을 완료하지 못했어요. 기존 조건과 결과를 유지했어요."
    if receipt.question:
        return receipt.question
    if receipt.goal == "edit_only":
        return (
            "조건을 변경했어요. 검색 결과는 다시 찾기 전 목록이에요."
            if receipt.filters_changed
            else "이미 적용된 조건이에요."
        )
    if receipt.selected and "place" in receipt.evidence:
        parts = [f"{receipt.evidence['place']}에 대해 알려드릴게요."]
        if receipt.goal == "pick_one":
            parts = [f"{receipt.evidence['place']}을 살펴보세요."]
        parts.extend(fact_sentence(f) for f in receipt.facts)
        if receipt.goal == "pick_one" or "selection_reason" in receipt.asked_attributes:
            basis = receipt.selection_basis
            parts.append(
                {
                    "visible_order": "보고 있는 목록의 첫 번째 후보를 골랐어요.",
                    "distance": "현재 검색 후보를 검색 중심과 가까운 순서로 보고 골랐어요.",
                    "parking_then_distance": "현재 검색 후보에서 주차 가능을 우선하고 거리를 기준으로 골랐어요.",
                    "user_reference": "사용자가 지정한 목록 위치의 장소예요.",
                }.get(
                    basis.method if basis else None, "이 장소를 선택한 이유는 기록되어 있지 않아요."
                )
            )
        return " ".join(parts)
    if receipt.returned_count == 0:
        return "현재 조건으로 찾은 장소가 없어요. 필수 조건은 그대로 유지했어요."
    return "현재 조건의 장소를 표시했어요."
