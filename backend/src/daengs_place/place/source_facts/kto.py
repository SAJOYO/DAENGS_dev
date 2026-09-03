"""KTO 목록+detailPetTour2 → 내부 source facts. DB·HTTP 호출 없음."""

import re

from daengs_place.place.source_facts.contract import (
    FactEvidence,
    PetAccessFacts,
    PetAmenityFacts,
    ProjectionIssue,
    PurposeFacts,
    RestrictionFacts,
    RestrictionPredicate,
    SourceFactProjection,
)
from daengs_place.place.source_facts.states import (
    EvidenceCertainty,
    FactState,
    ProjectionState,
)
from daengs_place.place.source_facts.taxonomy.kto import (
    hierarchy_is_valid,
    path_from,
    purpose_for,
)

PARSER_VERSION = "kto-source-facts/1"
_MISSING = {"", "정보없음", "-", "NULL"}
_WEIGHT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:kg|㎏)\s*(미만|이하)", re.IGNORECASE)
_NO_RESTRICTION = {"없음", "해당없음"}
_UNRESTRICTED_COMPANION = {"전 견종 동반 가능"}


def _text(value) -> str | None:
    result = str(value or "").strip()
    return None if result in _MISSING else result


def _evidence(
    source_field: str,
    raw_value,
    *,
    state: FactState,
    certainty: EvidenceCertainty = EvidenceCertainty.SOURCE,
    note: str | None = None,
) -> FactEvidence:
    return FactEvidence(
        state=state,
        source_field=source_field,
        raw_value=raw_value,
        parser_version=PARSER_VERSION,
        certainty=certainty,
        note=note,
    )

def _pred(
    code: str, applies_to: str = "all", *, params: dict[str, str] | None = None
) -> RestrictionPredicate:
    return RestrictionPredicate(code=code, applies_to=applies_to, params=params or {})


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


def _parse_need(raw: str) -> tuple[list[RestrictionPredicate], bool]:
    """통제어에 가까운 acmpyNeedMtr. bool은 원문을 전부 읽었는가."""

    result: list[RestrictionPredicate] = []
    complete = True
    for token in (part.strip() for part in raw.split(",")):
        if not token:
            continue
        found = False
        for word, code in (
            ("목줄", "require:leash"),
            ("입마개", "require:muzzle"),
            ("이동장", "require:carrier"),
            ("켄넬", "require:carrier"),
            ("유모차", "require:stroller"),
            ("매너벨트", "require:manner_belt"),
        ):
            if word in token:
                result.append(_pred(code))
                found = True
        if token == "기타" or not found:
            complete = False
    return result, complete


def _parse_companion(raw: str) -> tuple[list[RestrictionPredicate], bool]:
    """허용 대상 문장. 복합 자유문장은 읽은 술어가 있어도 partial로 둔다."""

    result: list[RestrictionPredicate] = []
    compact = re.sub(r"\s+", " ", raw).strip()
    if "맹견 제외" in compact or "맹견품종 제외" in compact:
        result.append(_pred("deny:breed", "breed:guard"))
    if "맹견의 경우" in compact and "입마개" in compact:
        result.append(_pred("require:muzzle", "breed:guard"))
    if "중소형견" in compact:
        result.append(_pred("deny:size", "size:large"))
    elif "소형견" in compact:
        result.append(_pred("deny:size", "size:medium_up"))
    if "이동장" in compact or "켄넬" in compact:
        result.append(_pred("require:carrier"))
    if "예방 접종" in compact or "예방접종" in compact or "광견병 접종" in compact:
        result.append(_pred("require:vaccination"))
    if "등록" in compact:
        result.append(_pred("admin:registration"))
    if match := _WEIGHT.search(compact):
        result.append(
            _pred(
                "deny:size",
                params={
                    "max_kg": match.group(1),
                    "inclusive": "true" if match.group(2) == "이하" else "false",
                },
            )
        )

    exact = {
        "전 견종 동반 가능",
        "전 견종 동반 가능(이동장 이용 필수)",
        "전 견종 출입 가능(맹견의 경우, 입마개 착용 필수)",
        "중소형견 동반 가능",
        "소형견 동반 가능",
        "이동장(켄넬)에 들어가는 전 견종 동반 가능",
        "맹견 제외 전 견종 동반 가능",
    }
    complete = compact in exact or bool(_WEIGHT.fullmatch(compact.replace(" 동반 가능", "")))
    # 수치·접종·등록이 결합된 문장은 술어를 보존하되 아직 완결이라고 주장하지 않는다.
    return result, complete


def _parse_free_text(raw: str) -> list[RestrictionPredicate]:
    """자유문장에서 안전한 앵커만 추출한다. 호출자는 항상 partial로 기록한다."""

    result: list[RestrictionPredicate] = []
    if "목줄" in raw:
        result.append(_pred("require:leash"))
    if "배변봉투" in raw or "배변처리" in raw:
        result.append(_pred("require:poop_bag"))
    if "맹견" in raw and "입마개" in raw:
        result.append(_pred("require:muzzle", "breed:guard"))
    if "접종" in raw:
        result.append(_pred("require:vaccination"))
    if "등록" in raw:
        result.append(_pred("admin:registration"))
    if re.search(r"실내.{0,12}(?:동반|출입)\s*불가", raw):
        result.append(_pred("zone:outdoor_only"))
    if match := re.search(r"([^\n,]{1,30}?)(?:은|는)\s*(?:동반|출입)\s*불가", raw):
        area = match.group(1).strip("- ")
        if area and "실내" not in area:
            result.append(_pred("zone:named_area", params={"name": area}))
    return result


def _explicit_no_restrictions(detail: dict) -> bool:
    """분산된 네 필드가 모두 명시적으로 무제약일 때만 확정한다."""

    values = {
        field: _text(detail.get(field))
        for field in (
            "acmpyNeedMtr",
            "acmpyPsblCpam",
            "etcAcmpyInfo",
            "relaAcdntRiskMtr",
        )
    }
    return (
        values["acmpyNeedMtr"] in _NO_RESTRICTION
        and values["acmpyPsblCpam"] in _UNRESTRICTED_COMPANION
        and values["etcAcmpyInfo"] in _NO_RESTRICTION
        and values["relaAcdntRiskMtr"] in _NO_RESTRICTION
    )


def _detail_state(detail: dict | None, requested: FactState) -> FactState:
    return FactState.KNOWN if detail else requested


def _detail_field_state(detail: dict, field: str, acquisition_state: FactState) -> FactState:
    """상세 획득 상태와 상세 안의 개별 필드 결측을 분리한다."""

    if _text(detail.get(field)) is not None:
        return FactState.KNOWN
    if acquisition_state is FactState.KNOWN:
        return FactState.NOT_PROVIDED
    return acquisition_state


def project_kto(
    listing: dict,
    detail: dict | None = None,
    *,
    detail_state: FactState = FactState.UNKNOWN,
) -> SourceFactProjection:
    """KTO 목록 원문과 선택적 상세 원문을 읽는다.

    현재 DB의 빈 `{}`는 미호출·실패·no-data를 구분하지 못하므로 기본 상태는 `unknown`이다.
    호출자가 acquisition metadata를 아는 경우에만 `not_fetched` 등을 명시한다.
    """

    detail = dict(detail or {})
    effective_detail_state = _detail_state(detail, detail_state)
    evidence: dict[str, FactEvidence] = {}
    issues: list[ProjectionIssue] = []

    content_type = _text(listing.get("contenttypeid"))
    primary = purpose_for(content_type)
    taxonomy = path_from(listing)
    purpose = PurposeFacts(
        primary=primary,
        subtype_code=taxonomy[-1].code if taxonomy else None,
        taxonomy_path=taxonomy,
    )
    evidence["purpose.primary"] = _evidence(
        "contenttypeid",
        listing.get("contenttypeid"),
        state=FactState.KNOWN
        if primary
        else (FactState.PARSE_FAILED if content_type else FactState.NOT_PROVIDED),
        certainty=EvidenceCertainty.DERIVED,
    )
    evidence["purpose.taxonomy_path"] = _evidence(
        "lclsSystm1/lclsSystm2/lclsSystm3",
        [listing.get("lclsSystm1"), listing.get("lclsSystm2"), listing.get("lclsSystm3")],
        state=FactState.KNOWN if taxonomy else FactState.NOT_PROVIDED,
    )
    if content_type and primary is None:
        issues.append(
            ProjectionIssue(
                code="unmapped_purpose",
                paths=("purpose.primary",),
                detail=f"알 수 없는 KTO contenttypeid: {content_type}",
            )
        )
    if taxonomy and not hierarchy_is_valid(taxonomy):
        issues.append(
            ProjectionIssue(
                code="invalid_taxonomy_path",
                paths=("purpose.taxonomy_path",),
                detail="KTO lcls 계층 코드의 접두 관계가 맞지 않음",
            )
        )

    scope_raw = _text(detail.get("acmpyTypeCd"))
    scope = {
        "전구역 동반가능": "full",
        "일부구역 동반가능": "partial",
    }.get(scope_raw or "")
    allowed = True if scope is not None else None
    zone_hints: set[str] = set()
    other_raw = _text(detail.get("etcAcmpyInfo"))
    companion_raw = _text(detail.get("acmpyPsblCpam"))
    if other_raw and re.search(r"실내.{0,12}(?:동반|출입)\s*불가", other_raw):
        zone_hints.add("outdoor")
    pet_access = PetAccessFacts(
        allowed=allowed,
        scope=scope,
        zone_hints=tuple(sorted(zone_hints)),
        companion_text=companion_raw,
    )
    scope_state = (
        FactState.KNOWN
        if scope is not None
        else FactState.PARSE_FAILED
        if scope_raw
        else _detail_field_state(detail, "acmpyTypeCd", effective_detail_state)
    )
    evidence["pet_access.scope"] = _evidence(
        "acmpyTypeCd",
        detail.get("acmpyTypeCd"),
        state=scope_state,
        certainty=EvidenceCertainty.DERIVED,
    )
    evidence["pet_access.allowed"] = _evidence(
        "acmpyTypeCd",
        detail.get("acmpyTypeCd"),
        state=scope_state,
        certainty=EvidenceCertainty.DERIVED,
        note="scope가 명시된 경우에만 동반 가능으로 투영",
    )
    evidence["pet_access.companion_text"] = _evidence(
        "acmpyPsblCpam",
        detail.get("acmpyPsblCpam"),
        state=_detail_field_state(detail, "acmpyPsblCpam", effective_detail_state),
    )
    if scope_raw and scope is None:
        issues.append(
            ProjectionIssue(
                code="unmapped_access_scope",
                paths=("pet_access.scope",),
                detail=f"알 수 없는 acmpyTypeCd: {scope_raw}",
            )
        )

    predicates: list[RestrictionPredicate] = []
    complete = True
    restriction_raw: dict[str, str] = {}

    need_raw = _text(detail.get("acmpyNeedMtr"))
    if need_raw and need_raw not in {"없음", "해당없음"}:
        restriction_raw["acmpyNeedMtr"] = need_raw
        parsed, field_complete = _parse_need(need_raw)
        predicates.extend(parsed)
        complete &= field_complete

    companion_predicates: list[RestrictionPredicate] = []
    if companion_raw and companion_raw not in {"없음", "해당없음"}:
        parsed, field_complete = _parse_companion(companion_raw)
        companion_predicates.extend(parsed)
        predicates.extend(parsed)
        # 허용 대상 문장 자체는 raw evidence지만, 제한 술어가 있을 때만 restriction raw다.
        if parsed:
            restriction_raw["acmpyPsblCpam"] = companion_raw
            complete &= field_complete

    if other_raw and other_raw not in {"없음", "해당없음"}:
        restriction_raw["etcAcmpyInfo"] = other_raw
        predicates.extend(_parse_free_text(other_raw))
        complete = False  # 자유문장은 안전한 앵커만 읽고 항상 원문을 함께 보낸다.

    risk_raw = _text(detail.get("relaAcdntRiskMtr"))
    if risk_raw and risk_raw not in {"없음", "해당없음"}:
        restriction_raw["relaAcdntRiskMtr"] = risk_raw
        predicates.extend(_parse_free_text(risk_raw))
        complete = False

    predicates_tuple = _dedupe(predicates)
    no_restrictions = _explicit_no_restrictions(detail)
    if predicates_tuple:
        restriction_state = "restricted"
        parse_state = "mapped" if complete else "partial"
    elif restriction_raw:
        restriction_state = "restricted"
        parse_state = "raw_only"
    elif no_restrictions:
        restriction_state = "none_confirmed"
        parse_state = None
    else:
        restriction_state = "unknown"
        parse_state = None
    restrictions = RestrictionFacts(
        state=restriction_state,
        parse_state=parse_state,
        predicates=predicates_tuple,
        raw=restriction_raw,
    )
    for field in (
        "acmpyNeedMtr",
        "acmpyPsblCpam",
        "etcAcmpyInfo",
        "relaAcdntRiskMtr",
    ):
        evidence[f"restrictions.source.{field}"] = _evidence(
            field,
            detail.get(field),
            state=_detail_field_state(detail, field, effective_detail_state),
        )
    evidence["restrictions.predicates"] = _evidence(
        "acmpyNeedMtr/acmpyPsblCpam/etcAcmpyInfo/relaAcdntRiskMtr",
        {
            field: detail[field]
            for field in (
                "acmpyNeedMtr",
                "acmpyPsblCpam",
                "etcAcmpyInfo",
                "relaAcdntRiskMtr",
            )
            if _text(detail.get(field)) is not None
        }
        or None,
        state=(
            FactState.KNOWN
            if restriction_raw or no_restrictions
            else (
                FactState.NOT_PROVIDED
                if effective_detail_state is FactState.KNOWN
                else effective_detail_state
            )
        ),
        certainty=EvidenceCertainty.DERIVED,
        note="KTO 여러 상세 필드에서 공통 predicate를 합성",
    )

    def amenity(field: str) -> tuple[str, ...]:
        value = _text(detail.get(field))
        return () if value is None or value in {"없음", "해당없음"} else (value,)

    amenities = PetAmenityFacts(
        facilities=amenity("relaPosesFclty"),
        provided_products=amenity("relaFrnshPrdlst"),
        purchasable_products=amenity("relaPurcPrdlst"),
        rentable_products=amenity("relaRntlPrdlst"),
    )
    for path, field in (
        ("amenities.facilities", "relaPosesFclty"),
        ("amenities.provided_products", "relaFrnshPrdlst"),
        ("amenities.purchasable_products", "relaPurcPrdlst"),
        ("amenities.rentable_products", "relaRntlPrdlst"),
    ):
        value = _text(detail.get(field))
        evidence[path] = _evidence(
            field,
            detail.get(field),
            state=(
                FactState.KNOWN
                if value
                else _detail_field_state(detail, field, effective_detail_state)
            ),
        )

    if restrictions.parse_state in {"partial", "raw_only"}:
        issues.append(
            ProjectionIssue(
                code="incomplete_restriction_parse",
                paths=("restrictions",),
                detail="KTO 제한 원문의 일부 또는 전부를 predicate로 완결하지 못함",
            )
        )

    return SourceFactProjection(
        source="kto",
        parser_version=PARSER_VERSION,
        state=ProjectionState.PARTIAL if issues else ProjectionState.COMPLETE,
        purpose=purpose,
        pet_access=pet_access,
        restrictions=restrictions,
        amenities=amenities,
        evidence=evidence,
        issues=tuple(issues),
    )
