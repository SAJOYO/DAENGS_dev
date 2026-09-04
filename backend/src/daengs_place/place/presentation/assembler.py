"""검색 hit와 같은 원천의 shadow facts를 semantic presentation으로 조립한다.

이 모듈은 서로 다른 원천 레코드를 연결하거나 값을 빌리지 않는다. ``PlaceResult``에 이미
빌린 값이 있더라도 정확한 link receipt가 없는 한 표시 facts로 채택하지 않는다.
"""

from collections.abc import Iterable

from daengs_place.geo.pet import SIZE_OPEN
from daengs_place.place.contracts import PlaceRef
from daengs_place.place.presentation.contract import (
    DecisionRole,
    EvaluationState,
    PlacePresentation,
    PresentationFact,
    PresentationFactId,
    PresentationNotice,
    PresentationProvenance,
    PresentationSeverity,
    SourceEvidenceSection,
    SourceRole,
    ValueOrigin,
    WhyMatchedReason,
)
from daengs_place.place.presentation.needs import InformationNeedId, information_need_spec
from daengs_place.place.presentation.policy import arrange_presentation
from daengs_place.place.restriction_map import LABELS, SUBJECT_LABELS, Subject
from daengs_place.place.search import PlaceSearchGroup, PlaceSearchHit
from daengs_place.place.source_facts.bundle import CandidateFactBundle, FactSection
from daengs_place.place.source_facts.contract import (
    FactEvidence,
    RestrictionPredicate,
    SourceFactProjection,
)
from daengs_place.place.source_facts.states import EvidenceCertainty, FactState, ProjectionState

_KIND_LABELS = {
    "hospital": "동물병원",
    "pharmacy": "동물약국",
    "pet_shop": "펫샵",
    "shopping": "쇼핑",
    "grooming": "미용",
    "boarding": "돌봄",
    "travel": "산책·야외",
    "leisure": "놀기",
    "museum": "박물관",
    "gallery": "미술관",
    "arts_center": "공연·문화",
    "culture": "문화공간",
    "cafe": "카페",
    "restaurant": "식당",
    "pension": "펜션",
    "hotel": "호텔",
    "stay": "숙소",
    "etc": "기타 장소",
}
_UNKNOWN_TEXT = {
    FactState.NOT_PROVIDED: "원천에서 제공하지 않음",
    FactState.NOT_FETCHED: "상세 정보 미수집",
    FactState.NOT_APPLICABLE: "해당 없음",
    FactState.PARSE_FAILED: "원문 확인 필요",
    FactState.UNKNOWN: "확인되지 않음",
}
_SAFETY_FACTS = {
    PresentationFactId.PET_ACCESS_ALLOWED,
    PresentationFactId.PET_SIZE,
    PresentationFactId.PET_RESTRICTIONS,
}


def _provenance(
    source: PlaceRef,
    evidence: FactEvidence | None = None,
    *,
    certainty: EvidenceCertainty | None = None,
    source_field: str | None = None,
) -> PresentationProvenance:
    return PresentationProvenance(
        source=source,
        source_role=SourceRole.PRIMARY,
        value_origin=ValueOrigin.OWN,
        evidence_certainty=certainty
        or (evidence.certainty if evidence is not None else EvidenceCertainty.SOURCE),
        source_field=source_field or (evidence.source_field if evidence is not None else None),
    )


def _fact(
    fact_id: PresentationFactId,
    label: str,
    value,
    display_text: str,
    source: PlaceRef,
    *,
    state: FactState = FactState.KNOWN,
    evidence: FactEvidence | None = None,
    certainty: EvidenceCertainty | None = None,
    source_field: str | None = None,
    evaluation: EvaluationState = EvaluationState.NOT_EVALUATED,
    roles: tuple[DecisionRole, ...] = (DecisionRole.DISPLAY,),
) -> PresentationFact:
    effective_value = value if state is FactState.KNOWN else None
    effective_text = display_text if state is FactState.KNOWN else _UNKNOWN_TEXT[state]
    return PresentationFact(
        fact_id=fact_id,
        label=label,
        value=effective_value,
        display_text=effective_text,
        source_state=state,
        evaluation_state=evaluation,
        provenance=_provenance(
            source,
            evidence,
            certainty=certainty,
            source_field=source_field,
        ),
        decision_roles=roles,
    )


def _evidence_fact(
    projection: SourceFactProjection,
    path: str,
    fact_id: PresentationFactId,
    label: str,
    value,
    display_text: str,
    source: PlaceRef,
    *,
    evaluation: EvaluationState = EvaluationState.NOT_EVALUATED,
    roles: tuple[DecisionRole, ...] = (DecisionRole.DISPLAY,),
) -> PresentationFact | None:
    evidence = projection.evidence.get(path)
    if evidence is None:
        return None
    state = evidence.state
    if state is FactState.KNOWN and value is None:
        state = FactState.UNKNOWN
    return _fact(
        fact_id,
        label,
        value,
        display_text,
        source,
        state=state,
        evidence=evidence,
        evaluation=evaluation,
        roles=roles,
    )


def _format_distance(distance_m: int) -> str:
    if distance_m < 1_000:
        return f"{distance_m}m"
    return f"{distance_m / 1_000:.1f}km"


def _base_facts(hit: PlaceSearchHit) -> list[PresentationFact]:
    place = hit.place
    source = place.key
    kind = place.match.kind
    facts = [
        _fact(
            PresentationFactId.PLACE_KIND,
            "장소 종류",
            kind,
            _KIND_LABELS.get(kind, kind),
            source,
            certainty=EvidenceCertainty.DERIVED,
            roles=(DecisionRole.DISPLAY, DecisionRole.FILTERING),
        ),
        _fact(
            PresentationFactId.PLACE_DISTANCE,
            "거리",
            place.distance_m,
            _format_distance(place.distance_m),
            source,
            certainty=EvidenceCertainty.DERIVED,
            roles=(DecisionRole.DISPLAY, DecisionRole.RANKING),
        ),
    ]
    if place.facts.address:
        facts.append(
            _fact(
                PresentationFactId.PLACE_ADDRESS,
                "주소",
                place.facts.address,
                place.facts.address,
                source,
            )
        )
    if place.facts.phone:
        facts.append(
            _fact(
                PresentationFactId.CONTACT_PHONE,
                "전화",
                place.facts.phone,
                place.facts.phone,
                source,
            )
        )
    if place.facts.homepage and "facts.homepage" not in place.field_sources:
        facts.append(
            _fact(
                PresentationFactId.CONTACT_HOMEPAGE,
                "홈페이지",
                place.facts.homepage,
                place.facts.homepage,
                source,
            )
        )

    medical = place.facts.medical
    if medical is not None:
        if medical.open_now is not None:
            facts.append(
                _fact(
                    PresentationFactId.OPERATIONS_OPEN_NOW,
                    "현재 영업",
                    medical.open_now,
                    "영업 중" if medical.open_now else "영업 종료",
                    source,
                    certainty=EvidenceCertainty.DERIVED,
                )
            )
        for path, fact_id, label, value in (
            (
                "facts.hours_text",
                PresentationFactId.OPERATIONS_HOURS,
                "운영시간",
                place.facts.hours_text,
            ),
            (
                "facts.closed_days",
                PresentationFactId.OPERATIONS_CLOSED_DAYS,
                "휴무일",
                place.facts.closed_days,
            ),
        ):
            if value and path not in place.field_sources:
                facts.append(_fact(fact_id, label, value, value, source))
    return facts


def _display_number(value: str) -> str:
    try:
        number = float(value)
    except ValueError:
        return value
    return str(int(number)) if number.is_integer() else str(number)


def _predicate_text(predicate: RestrictionPredicate) -> str:
    base = LABELS.get(predicate.code, predicate.code)
    params = predicate.params
    detail = None
    if predicate.code == "deny:size" and "max_kg" in params:
        boundary = "이하" if params.get("inclusive") == "true" else "미만"
        detail = f"{_display_number(params['max_kg'])}kg {boundary}"
    elif predicate.code == "deny:size" and "min_kg" in params:
        detail = f"{_display_number(params['min_kg'])}kg 이상"
    elif predicate.code == "deny:age" and "max_months" in params:
        detail = f"{_display_number(params['max_months'])}개월 기준"
    elif predicate.code == "deny:age" and "min_years" in params:
        detail = f"{_display_number(params['min_years'])}살 이상"
    elif predicate.code == "limit:max_dogs" and "max" in params:
        detail = f"최대 {_display_number(params['max'])}마리"
    elif predicate.code == "fee:deposit" and "amount" in params:
        try:
            detail = f"예치금 {int(params['amount']):,}원"
        except ValueError:
            detail = f"예치금 {params['amount']}원"
    try:
        qualifier = SUBJECT_LABELS[Subject(predicate.applies_to)]
    except ValueError:
        qualifier = predicate.applies_to
    return "·".join(item for item in (base, detail, qualifier) if item)


def _restriction_text(predicates: Iterable[RestrictionPredicate]) -> str:
    predicates = tuple(predicates)
    if not predicates:
        return "확인된 제한 조건 없음"
    labels = [_predicate_text(item) for item in predicates]
    return " · ".join(dict.fromkeys(labels))[:500]


def _restriction_evidence(projection: SourceFactProjection) -> FactEvidence | None:
    direct = projection.evidence.get("restrictions.predicates")
    if direct is not None:
        return direct
    paths = ["restrictions.text"]
    if projection.restrictions.predicates:
        paths.append("restrictions.size")
    candidates = tuple(
        evidence for path in paths if (evidence := projection.evidence.get(path)) is not None
    )
    known = tuple(item for item in candidates if item.state is FactState.KNOWN)
    if len(known) == 1:
        return known[0]
    if len(known) > 1:
        return FactEvidence(
            state=FactState.KNOWN,
            source_field="/".join(item.source_field for item in known),
            raw_value=[item.raw_value for item in known],
            parser_version=projection.parser_version,
            certainty=EvidenceCertainty.DERIVED,
            note="여러 KCISA 제한 필드에서 공통 predicate를 합성",
        )
    parse_failed = next(
        (item for item in candidates if item.state is FactState.PARSE_FAILED),
        None,
    )
    return parse_failed or (candidates[0] if candidates else None)


def _projection_facts(
    projection: SourceFactProjection,
    hit: PlaceSearchHit,
    group: PlaceSearchGroup,
    allowed_sections: set[FactSection],
) -> list[PresentationFact]:
    source = hit.place.key
    facts: list[PresentationFact] = []

    if "pet_access" in allowed_sections:
        access = projection.pet_access
        values = (
            (
                "pet_access.allowed",
                PresentationFactId.PET_ACCESS_ALLOWED,
                "반려견 동반",
                access.allowed,
                "동반 가능" if access.allowed else "동반 불가",
                EvaluationState.NOT_EVALUATED,
            ),
            (
                "pet_access.scope",
                PresentationFactId.PET_ACCESS_SCOPE,
                "동반 가능 범위",
                access.scope,
                {"full": "전 구역 동반 가능", "partial": "일부 구역 동반 가능"}.get(
                    access.scope or "", "확인되지 않음"
                ),
                EvaluationState.NOT_EVALUATED,
            ),
            (
                "pet_access.zone_hints",
                PresentationFactId.PET_ACCESS_ZONES,
                "동반 구역",
                list(access.zone_hints),
                " · ".join(
                    {"indoor": "실내", "outdoor": "야외"}[item] for item in access.zone_hints
                )
                or "확인된 동반 구역 없음",
                EvaluationState.NOT_EVALUATED,
            ),
            (
                "pet_access.companion_text",
                PresentationFactId.PET_ACCESS_COMPANION,
                "동반 대상",
                access.companion_text,
                access.companion_text or "확인되지 않음",
                EvaluationState.NOT_EVALUATED,
            ),
        )
        for path, fact_id, label, value, display, evaluation in values:
            fact = _evidence_fact(
                projection,
                path,
                fact_id,
                label,
                value,
                display,
                source,
                evaluation=evaluation,
                roles=(DecisionRole.DISPLAY, DecisionRole.EVALUATION)
                if evaluation is not EvaluationState.NOT_EVALUATED
                else (DecisionRole.DISPLAY,),
            )
            if fact is not None:
                facts.append(fact)

    if "restrictions" in allowed_sections:
        restrictions = projection.restrictions
        predicate_value = [item.model_dump(mode="json") for item in restrictions.predicates]
        restriction_evidence = _restriction_evidence(projection)
        if restriction_evidence is not None:
            facts.append(
                _fact(
                    PresentationFactId.PET_RESTRICTIONS,
                    "이용 조건",
                    predicate_value,
                    _restriction_text(restrictions.predicates),
                    source,
                    state=restriction_evidence.state,
                    evidence=restriction_evidence,
                )
            )
        size_predicates = tuple(
            item for item in restrictions.predicates if item.code == "deny:size"
        )
        size_evidence = projection.evidence.get("restrictions.size")
        if size_evidence is None and size_predicates:
            size_evidence = projection.evidence.get("restrictions.predicates")
        if size_evidence is not None:
            state = size_evidence.state
            explicitly_open = state is FactState.KNOWN and SIZE_OPEN in str(
                size_evidence.raw_value or ""
            )
            value = (
                "any"
                if explicitly_open
                else [item.model_dump(mode="json") for item in size_predicates]
            )
            display = "모든 크기 가능" if explicitly_open else _restriction_text(size_predicates)
            facts.append(
                _fact(
                    PresentationFactId.PET_SIZE,
                    "크기 제한",
                    value,
                    display,
                    source,
                    state=state,
                    evidence=size_evidence,
                )
            )

    if "amenities" in allowed_sections:
        amenity_values = (
            (
                "amenities.facilities",
                PresentationFactId.PET_AMENITIES_FACILITIES,
                "반려견 시설",
                projection.amenities.facilities,
            ),
            (
                "amenities.provided_products",
                PresentationFactId.PET_PRODUCTS_PROVIDED,
                "제공 용품",
                projection.amenities.provided_products,
            ),
            (
                "amenities.purchasable_products",
                PresentationFactId.PET_PRODUCTS_PURCHASABLE,
                "판매 용품",
                projection.amenities.purchasable_products,
            ),
            (
                "amenities.rentable_products",
                PresentationFactId.PET_PRODUCTS_RENTABLE,
                "대여 용품",
                projection.amenities.rentable_products,
            ),
        )
        for path, fact_id, label, values in amenity_values:
            fact = _evidence_fact(
                projection,
                path,
                fact_id,
                label,
                list(values),
                " · ".join(values) or "확인된 항목 없음",
                source,
            )
            if fact is not None:
                facts.append(fact)

    if "pet_fee" in allowed_sections:
        fee = projection.pet_fee
        evidence = projection.evidence.get("pet_fee.amount_krw")
        if evidence is not None:
            display = (
                "무료"
                if fee.amount_krw == 0
                else f"{fee.amount_krw:,}원"
                if fee.amount_krw is not None
                else fee.raw_text or "확인되지 않음"
            )
            facts.append(
                _fact(
                    PresentationFactId.PET_FEE,
                    "반려견 추가요금",
                    fee.amount_krw,
                    display,
                    source,
                    state=evidence.state,
                    evidence=evidence,
                )
            )

    if "operations" in allowed_sections:
        operations = projection.operations
        operation_values = (
            (
                "operations.parking",
                PresentationFactId.OPERATIONS_PARKING,
                "주차",
                operations.parking,
                "주차 가능" if operations.parking else "주차 불가",
            ),
            (
                "operations.hours_text",
                PresentationFactId.OPERATIONS_HOURS,
                "운영시간",
                operations.hours_text,
                operations.hours_text or "확인되지 않음",
            ),
            (
                "operations.closed_days",
                PresentationFactId.OPERATIONS_CLOSED_DAYS,
                "휴무일",
                operations.closed_days,
                operations.closed_days or "확인되지 않음",
            ),
        )
        for path, fact_id, label, value, display in operation_values:
            roles = (DecisionRole.DISPLAY,)
            if (
                fact_id is PresentationFactId.OPERATIONS_PARKING
                and "parking" in group.sort.applied
                and "facts.parking" not in hit.place.field_sources
            ):
                roles = (DecisionRole.DISPLAY, DecisionRole.RANKING)
            fact = _evidence_fact(
                projection,
                path,
                fact_id,
                label,
                value,
                display,
                source,
                roles=roles,
            )
            if fact is not None:
                facts.append(fact)
    return facts


def _bundle_projection(
    bundle: CandidateFactBundle | None,
) -> tuple[SourceFactProjection | None, set[FactSection], tuple[PresentationNotice, ...]]:
    if bundle is None:
        return None, set(), ()
    if bundle.availability == "missing":
        return (
            None,
            set(),
            (
                PresentationNotice(
                    code="source_facts.missing",
                    message="이 장소의 원천 상세 사실이 아직 적재되지 않았습니다.",
                    severity=PresentationSeverity.WARNING,
                ),
            ),
        )
    successful = [
        item.projection
        for item in bundle.variants
        if item.projection.state is not ProjectionState.FAILED
    ]
    if not successful:
        return (
            None,
            set(),
            (
                PresentationNotice(
                    code="source_facts.projection_failed",
                    message="원천 상세 사실을 해석하지 못해 기본 정보만 표시합니다.",
                    severity=PresentationSeverity.WARNING,
                ),
            ),
        )
    conflicted = {item.section for item in bundle.conflicts}
    allowed = {
        section
        for section in (
            "purpose",
            "pet_access",
            "restrictions",
            "amenities",
            "pet_fee",
            "operations",
        )
        if section not in conflicted
    }
    notices = [
        PresentationNotice(
            code=f"source_facts.{section}.conflict",
            message=f"같은 원천 후보의 {section} 값이 서로 달라 해당 상세 정보는 표시하지 않습니다.",
            severity=PresentationSeverity.WARNING,
        )
        for section in sorted(conflicted)
    ]
    for section in sorted(allowed):
        prefix = f"{section}."
        signatures = {
            tuple(
                sorted(
                    (
                        path,
                        evidence.state,
                        evidence.source_field,
                        evidence.certainty,
                    )
                    for path, evidence in projection.evidence.items()
                    if path.startswith(prefix)
                )
            )
            for projection in successful
        }
        if len(signatures) > 1:
            allowed.remove(section)
            notices.append(
                PresentationNotice(
                    code=f"source_facts.{section}.evidence_conflict",
                    message=(
                        f"같은 원천 후보의 {section} 획득 상태가 서로 달라 "
                        "해당 상세 정보는 표시하지 않습니다."
                    ),
                    severity=PresentationSeverity.WARNING,
                )
            )
    failed_count = len(bundle.variants) - len(successful)
    if failed_count:
        notices.append(
            PresentationNotice(
                code="source_facts.variant_failures",
                message=f"같은 원천 후보 중 {failed_count}개 레코드의 상세 사실을 해석하지 못했습니다.",
                severity=PresentationSeverity.WARNING,
            )
        )
    issue_codes = {issue.code for projection in successful for issue in projection.issues}
    if issue_codes:
        notices.append(
            PresentationNotice(
                code="source_facts.projection_issues",
                message="원천 상세의 일부를 완전히 해석하지 못했습니다: "
                + ", ".join(sorted(issue_codes)),
                severity=PresentationSeverity.WARNING,
            )
        )
    return successful[0], allowed, tuple(notices)


def _source_evidence(facts: Iterable[PresentationFact], source: PlaceRef) -> SourceEvidenceSection:
    return SourceEvidenceSection(
        source=source,
        source_role=SourceRole.PRIMARY,
        adopted_fact_ids=tuple(fact.fact_id for fact in facts),
    )


def _visible_facts(
    facts: list[PresentationFact],
    information_needs: tuple[InformationNeedId, ...],
) -> tuple[PresentationFact, ...]:
    requested = {
        fact_id
        for need_id in information_needs
        for fact_id in information_need_spec(need_id).promoted_fact_ids
    }
    return tuple(
        fact
        for fact in facts
        if fact.source_state is FactState.KNOWN
        or fact.fact_id in _SAFETY_FACTS
        or fact.fact_id in requested
    )


def assemble_place_presentation(
    hit: PlaceSearchHit,
    group: PlaceSearchGroup,
    *,
    lens_id: str,
    lens_label: str,
    lens_support_note: str,
    information_needs: tuple[InformationNeedId, ...] = (),
    source_facts: CandidateFactBundle | None = None,
) -> PlacePresentation:
    """검색 후보를 삭제하지 않고, 검증 가능한 자체 원천 사실만 표시 모델로 옮긴다."""

    if source_facts is not None and (
        source_facts.key.source != hit.place.key.source
        or source_facts.key.source_ref != hit.place.key.ref
    ):
        raise ValueError("source fact bundle must belong to the presentation place key")
    facts = _base_facts(hit)
    projection, allowed_sections, assembly_notices = _bundle_projection(source_facts)
    if projection is not None:
        facts.extend(_projection_facts(projection, hit, group, allowed_sections))

    fact_ids = [fact.fact_id for fact in facts]
    if len(set(fact_ids)) != len(fact_ids):
        raise ValueError("presentation assembly produced duplicate fact ids")
    arranged = arrange_presentation(_visible_facts(facts, information_needs), information_needs)
    notices = {item.code: item for item in (*arranged.notices, *assembly_notices)}
    place = hit.place
    kind_label = _KIND_LABELS.get(place.match.kind, place.match.kind)
    all_items = (*arranged.core_items, *arranged.promoted_items, *arranged.detail_items)
    evidence = (_source_evidence(all_items, place.key),) if all_items else ()
    return PlacePresentation(
        place_key=place.key,
        title=place.name,
        summary=f"{kind_label} · {_format_distance(place.distance_m)}",
        kind_id=place.match.kind,
        kind_label=kind_label,
        distance_m=place.distance_m,
        address=place.facts.address,
        core_items=arranged.core_items,
        promoted_items=arranged.promoted_items,
        detail_items=arranged.detail_items,
        notices=tuple(notices.values()),
        why_matched=(
            WhyMatchedReason(
                code="intent.lens",
                message=f"{lens_label}: {lens_support_note}",
                receipt_ids=(lens_id,),
            ),
        ),
        source_evidence=evidence,
        policy_receipt=arranged.policy_receipt,
    )


__all__ = ["assemble_place_presentation"]
