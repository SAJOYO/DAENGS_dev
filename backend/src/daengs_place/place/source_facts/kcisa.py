"""KCISA CSV 한 행 → 내부 source facts. DB·ingest 호출 없음."""

import re

from daengs_place.geo.pet import SIZE_OPEN, SIZE_UNKNOWN, derive_axes
from daengs_place.place.restriction_map import derive
from daengs_place.place.source_catalog import KCISA_KINDS
from daengs_place.place.source_facts.contract import (
    FactEvidence,
    OperationFacts,
    PetAccessFacts,
    PetAmenityFacts,
    PetFeeFacts,
    ProjectionIssue,
    PurposeFacts,
    RestrictionFacts,
    RestrictionPredicate,
    SourceFactProjection,
    TaxonomyNode,
)
from daengs_place.place.source_facts.states import (
    EvidenceCertainty,
    FactState,
    ProjectionState,
)

PARSER_VERSION = "kcisa-source-facts/1"
_MISSING = {"", "정보없음", "-", "NULL"}
_EXACT_WON = re.compile(r"(\d[\d,]*)\s*원")
_SIZE_BOUNDARY = re.compile(r"(\d+(?:\.\d+)?)\s*(?:kg|㎏)\s*(미만|이하)", re.IGNORECASE)


def _text(value) -> str | None:
    result = str(value or "").strip()
    return None if result in _MISSING else result


def _flag(value) -> bool | None:
    text = _text(value)
    if text is None:
        return None
    head = text[:1].upper()
    return True if head == "Y" else False if head == "N" else None


def _evidence(
    source_field: str,
    raw_value,
    *,
    state: FactState | None = None,
    certainty: EvidenceCertainty = EvidenceCertainty.SOURCE,
    note: str | None = None,
) -> FactEvidence:
    if state is None:
        state = FactState.KNOWN if _text(raw_value) is not None else FactState.NOT_PROVIDED
    return FactEvidence(
        state=state,
        source_field=source_field,
        raw_value=raw_value,
        parser_version=PARSER_VERSION,
        certainty=certainty,
        note=note,
    )

def _predicate(value) -> RestrictionPredicate:
    return RestrictionPredicate(
        code=value.code,
        applies_to=value.applies_to.value,
        params=dict(value.params),
        certainty=value.certainty.value,
    )


def _dedupe(values: list[RestrictionPredicate]) -> tuple[RestrictionPredicate, ...]:
    seen: set[tuple] = set()
    result = []
    for value in values:
        key = (
            value.code,
            value.applies_to,
            tuple(sorted(value.params.items())),
            value.certainty,
        )
        if key not in seen:
            seen.add(key)
            result.append(value)
    return tuple(result)


def _size_predicates(size: str | None) -> list[RestrictionPredicate]:
    axes = derive_axes({"size": size} if size else {})
    result = []
    if axes.dog_ok is False:
        result.append(RestrictionPredicate(code="deny:species_dog"))
    if axes.size_class in ("small", "medium"):
        applies_to = "size:medium_up" if axes.size_class == "small" else "size:large"
        params = {"max_kg": str(axes.max_kg)} if axes.max_kg is not None else {}
        boundaries = [
            (float(value), operator) for value, operator in _SIZE_BOUNDARY.findall(size or "")
        ]
        boundary = next(
            (
                operator
                for value, operator in reversed(boundaries)
                if axes.max_kg is not None and value == axes.max_kg
            ),
            None,
        )
        if boundary is not None:
            params["inclusive"] = "true" if boundary == "이하" else "false"
        result.append(RestrictionPredicate(code="deny:size", applies_to=applies_to, params=params))
    return result


def _fee(raw) -> tuple[PetFeeFacts, FactEvidence]:
    text = _text(raw)
    if text is None:
        return PetFeeFacts(), _evidence("애견 동반 추가 요금", raw)
    if text in {"없음", "무료"}:
        return PetFeeFacts(raw_text=text, amount_krw=0), _evidence(
            "애견 동반 추가 요금",
            raw,
            certainty=EvidenceCertainty.DERIVED,
        )
    if text == "해당없음":
        return PetFeeFacts(raw_text=text), _evidence(
            "애견 동반 추가 요금", raw, state=FactState.NOT_APPLICABLE
        )
    match = _EXACT_WON.fullmatch(text)
    if match:
        return PetFeeFacts(
            raw_text=text, amount_krw=int(match.group(1).replace(",", ""))
        ), _evidence(
            "애견 동반 추가 요금",
            raw,
            certainty=EvidenceCertainty.DERIVED,
        )
    return PetFeeFacts(raw_text=text), _evidence(
        "애견 동반 추가 요금",
        raw,
        state=FactState.PARSE_FAILED,
        certainty=EvidenceCertainty.DERIVED,
        note="단일 확정 원화 금액으로 읽을 수 없는 원문",
    )


def project_kcisa(row: dict) -> SourceFactProjection:
    """공식 KCISA CSV 원문 한 행을 읽는다. 입력 dict를 수정하지 않는다."""

    evidence: dict[str, FactEvidence] = {}
    issues: list[ProjectionIssue] = []

    categories = [_text(row.get(field)) for field in ("카테고리1", "카테고리2", "카테고리3")]
    category3 = categories[-1]
    primary = KCISA_KINDS.get(category3 or "") if category3 else None
    purpose = PurposeFacts(
        primary=primary,
        subtype_code=category3,
        taxonomy_path=tuple(TaxonomyNode(code=value, label=value) for value in categories if value),
    )
    evidence["purpose.primary"] = _evidence(
        "카테고리3",
        row.get("카테고리3"),
        state=FactState.KNOWN
        if primary
        else (FactState.PARSE_FAILED if category3 else FactState.NOT_PROVIDED),
        certainty=EvidenceCertainty.DERIVED,
    )
    evidence["purpose.taxonomy_path"] = _evidence(
        "카테고리1/카테고리2/카테고리3",
        [row.get("카테고리1"), row.get("카테고리2"), row.get("카테고리3")],
    )
    if category3 and primary is None:
        issues.append(
            ProjectionIssue(
                code="unmapped_purpose",
                paths=("purpose.primary",),
                detail=f"알 수 없는 KCISA 카테고리3: {category3}",
            )
        )

    allowed_raw = row.get("반려동물 동반 가능정보")
    allowed = _flag(allowed_raw)
    exclusive_raw = _text(row.get("반려동물 전용 정보"))
    exclusive = (
        True if exclusive_raw == "반려동물 전용" else False if exclusive_raw == "해당없음" else None
    )
    indoor = _flag(row.get("장소(실내) 여부"))
    outdoor = _flag(row.get("장소(실외)여부"))
    zone_hints = tuple(
        zone for zone, value in (("indoor", indoor), ("outdoor", outdoor)) if value is True
    )
    pet_access = PetAccessFacts(
        allowed=allowed,
        exclusive=exclusive,
        source_indoor=indoor,
        source_outdoor=outdoor,
        zone_hints=zone_hints,
    )
    evidence["pet_access.allowed"] = _evidence(
        "반려동물 동반 가능정보",
        allowed_raw,
        state=FactState.KNOWN
        if allowed is not None
        else (FactState.NOT_PROVIDED if _text(allowed_raw) is None else FactState.PARSE_FAILED),
    )
    evidence["pet_access.exclusive"] = _evidence(
        "반려동물 전용 정보",
        row.get("반려동물 전용 정보"),
        state=FactState.KNOWN
        if exclusive is not None
        else (FactState.NOT_PROVIDED if exclusive_raw is None else FactState.PARSE_FAILED),
    )
    evidence["pet_access.source_indoor"] = _evidence(
        "장소(실내) 여부",
        row.get("장소(실내) 여부"),
        state=FactState.KNOWN
        if indoor is not None
        else (
            FactState.NOT_PROVIDED
            if _text(row.get("장소(실내) 여부")) is None
            else FactState.PARSE_FAILED
        ),
    )
    evidence["pet_access.source_outdoor"] = _evidence(
        "장소(실외)여부",
        row.get("장소(실외)여부"),
        state=FactState.KNOWN
        if outdoor is not None
        else (
            FactState.NOT_PROVIDED
            if _text(row.get("장소(실외)여부")) is None
            else FactState.PARSE_FAILED
        ),
    )
    evidence["pet_access.zone_hints"] = _evidence(
        "장소(실내) 여부/장소(실외)여부",
        [row.get("장소(실내) 여부"), row.get("장소(실외)여부")],
        state=FactState.KNOWN
        if indoor is not None or outdoor is not None
        else FactState.NOT_PROVIDED,
        certainty=EvidenceCertainty.DERIVED,
        note="원천 플래그의 구역 해석이며 명시적 제한 문장이 우선함",
    )

    restriction_raw = _text(row.get("반려동물 제한사항"))
    derivation = derive(restriction_raw)
    predicates = [_predicate(value) for value in derivation.predicates]
    size_raw = _text(row.get("입장 가능 동물 크기"))
    size_predicates = _size_predicates(size_raw)
    predicates.extend(size_predicates)
    restriction_state = derivation.state.value
    parse_state = derivation.parse_state.value if derivation.parse_state is not None else None
    if size_predicates:
        restriction_state = "restricted"
        parse_state = parse_state or "mapped"
    raw = {}
    if restriction_raw:
        raw["반려동물 제한사항"] = restriction_raw
    if size_raw:
        raw["입장 가능 동물 크기"] = size_raw
    restrictions = RestrictionFacts(
        state=restriction_state,
        parse_state=parse_state,
        predicates=_dedupe(predicates),
        raw=raw,
    )
    evidence["restrictions.text"] = _evidence(
        "반려동물 제한사항",
        row.get("반려동물 제한사항"),
        state=(
            FactState.NOT_APPLICABLE
            if derivation.state.value == "not_applicable"
            else FactState.KNOWN
            if restriction_raw
            else FactState.NOT_PROVIDED
        ),
        certainty=EvidenceCertainty.DERIVED,
    )
    evidence["restrictions.size"] = _evidence(
        "입장 가능 동물 크기",
        row.get("입장 가능 동물 크기"),
        state=(
            FactState.NOT_PROVIDED
            if size_raw is None
            else FactState.NOT_APPLICABLE
            if size_raw == SIZE_UNKNOWN
            else FactState.KNOWN
            if size_predicates or SIZE_OPEN in size_raw
            else FactState.PARSE_FAILED
        ),
        certainty=EvidenceCertainty.DERIVED,
        note=(
            "크기 허용 범위로 안전하게 읽을 수 없는 원문"
            if size_raw
            and size_raw != SIZE_UNKNOWN
            and not size_predicates
            and SIZE_OPEN not in size_raw
            else None
        ),
    )
    if size_raw and size_raw != SIZE_UNKNOWN and not size_predicates and SIZE_OPEN not in size_raw:
        issues.append(
            ProjectionIssue(
                code="unparsed_size_constraint",
                paths=("restrictions.size",),
                detail=f"크기 허용 범위로 읽을 수 없는 KCISA 원문: {size_raw}",
            )
        )

    codes = {value.code for value in restrictions.predicates}
    if "zone:outdoor_only" in codes and indoor is True:
        issues.append(
            ProjectionIssue(
                code="zone_flag_conflict",
                paths=("pet_access.zone_hints", "restrictions.predicates"),
                detail="실내 플래그는 Y지만 제한사항은 야외/실외만 허용한다고 명시함",
            )
        )
    if allowed is False and (indoor is True or outdoor is True):
        issues.append(
            ProjectionIssue(
                code="denied_with_zone_flag",
                paths=("pet_access.allowed", "pet_access.zone_hints"),
                detail="동반 불가 행에 이용 가능 구역으로 읽힐 수 있는 플래그가 있음",
            )
        )

    fee, fee_evidence = _fee(row.get("애견 동반 추가 요금"))
    evidence["pet_fee.amount_krw"] = fee_evidence

    parking_raw = row.get("주차 가능여부")
    parking = _flag(parking_raw)
    operations = OperationFacts(
        hours_text=_text(row.get("운영시간")),
        closed_days=_text(row.get("휴무일")),
        parking=parking,
    )
    evidence["operations.hours_text"] = _evidence("운영시간", row.get("운영시간"))
    evidence["operations.closed_days"] = _evidence("휴무일", row.get("휴무일"))
    evidence["operations.parking"] = _evidence(
        "주차 가능여부",
        parking_raw,
        state=FactState.KNOWN
        if parking is not None
        else (FactState.NOT_PROVIDED if _text(parking_raw) is None else FactState.PARSE_FAILED),
    )

    return SourceFactProjection(
        source="kcisa",
        parser_version=PARSER_VERSION,
        state=ProjectionState.PARTIAL if issues else ProjectionState.COMPLETE,
        purpose=purpose,
        pet_access=pet_access,
        restrictions=restrictions,
        amenities=PetAmenityFacts(),
        pet_fee=fee,
        operations=operations,
        evidence=evidence,
        issues=tuple(issues),
    )
