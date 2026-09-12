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


def render_answer(receipt, filters=None):
    text = _render_result(receipt, filters)
    if receipt.feedback == "information_dispute" and "현장과 다를" not in text:
        # A correction does not prove closure or relocation. Keep this fact alongside the action.
        if receipt.execution == "failed":
            return "정보가 현장과 다를 수 있어요. 검색을 완료하지 못해 목록은 그대로예요."
        return "정보가 현장과 다를 수 있어요. " + text
    return text


def _render_result(receipt, filters):
    if receipt.bookmark_command is not None:
        return "찜 처리 결과를 확인해 주세요."
    if receipt.execution == "failed":
        if receipt.known_places:
            return "이미 아는 곳으로 반영했어요. 다시 찾지 못해 목록은 그대로예요."
        return "검색을 완료하지 못했어요. 보던 목록은 그대로예요."
    if receipt.question:
        return receipt.question
    if receipt.known_places:
        if receipt.execution == "not_run":
            return "이미 아는 곳으로 반영했고, 목록은 그대로예요."
        if receipt.returned_count:
            return f"이미 아는 곳을 반영해 새 후보 {receipt.returned_count}곳 찾아뒀어요!"
        return "이미 아는 곳으로 반영했지만, 새 후보는 더 찾지 못했어요."
    if receipt.browse != "current" or receipt.excluded_places or receipt.restored_places:
        prefix = ""
        if receipt.excluded_places:
            prefix = f"{len(receipt.excluded_places)}곳을 제외하고 "
        elif receipt.restored_places:
            prefix = f"{len(receipt.restored_places)}곳을 다시 포함해 "
        elif receipt.browse == "restart":
            prefix = "처음부터 다시 살펴보고 "
        if receipt.browse == "next":
            if receipt.new_places:
                return prefix + f"다른 후보 {len(receipt.new_places)}곳 찾아뒀어요!"
            return prefix + "다른 후보는 더 찾지 못했어요."
        if receipt.returned_count:
            return prefix + f"조건에 맞는 {receipt.returned_count}곳 찾아뒀어요!"
        return prefix + "조건에 맞는 곳은 찾지 못했어요."
    if receipt.goal == "edit_only":
        return (
            "조건을 바꿨어요. 목록은 아직 그대로예요."
            if receipt.filters_changed
            else "이미 적용된 조건이에요."
        )
    if receipt.selected and "place" in receipt.evidence:
        name = receipt.evidence["place"]
        if receipt.goal == "pick_one":
            return f"{name} 골라뒀어요!"
        statements = _explanation(receipt)
        return " ".join(statements[:2]) if statements else f"{name}의 정보를 카드에 담아뒀어요."
    if receipt.returned_count == 0:
        return "지금 조건에 맞는 곳은 더 찾지 못했어요."
    if receipt.search_pool in {"unbookmarked", "new_candidates"}:
        label = "찜하지 않은 곳" if receipt.search_pool == "unbookmarked" else "새 후보"
    else:
        kinds = filters.candidate_kinds if filters is not None else ()
        label = KINDS.get(kinds[0], "장소") if len(kinds) == 1 else "장소"
    verb = "찾아뒀어요!" if receipt.execution == "searched" else "보여드릴게요."
    return f"조건에 맞는 {label} {receipt.returned_count}곳 {verb}"


def _explanation(receipt):
    statements = [fact_sentence(f) for f in receipt.facts if f.status == "known"]
    for status in ("unknown", "unsupported"):
        labels = [
            ATTRIBUTES.get(f.attribute, "요청한 정보") for f in receipt.facts if f.status == status
        ]
        if labels:
            text = "·".join(labels)
            statements.append(
                f"{text} 정보는 없어서 확인이 필요해요."
                if status == "unknown"
                else f"{text}는 지금 자료로 확인할 수 없어요."
            )
    if "selection_reason" in receipt.asked_attributes:
        basis = receipt.selection_basis
        reason = {
            "visible_order": "보고 있는 목록의 첫 번째 후보를 골랐어요.",
            "distance": "검색 중심에서 가까운 후보를 골랐어요.",
            "parking_then_distance": "주차 가능한 곳을 우선해 가까운 후보를 골랐어요.",
            "user_reference": "말씀하신 순서의 장소를 골랐어요.",
        }.get(basis.method if basis else None, "선택 이유는 기록되어 있지 않아요.")
        # An explicitly asked selection reason must survive the two-sentence display budget.
        statements = statements[:1] + [reason]
    return statements
