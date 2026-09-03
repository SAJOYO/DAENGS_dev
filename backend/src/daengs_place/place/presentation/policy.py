"""원천 선택을 하지 않고 effective fact의 표시 위치만 정하는 순수 정책."""

from daengs_place.place.presentation.contract import (
    DecisionRole,
    EvaluationState,
    LinkState,
    PresentationFact,
    PresentationFactId,
    PresentationItem,
    PresentationNotice,
    PresentationPlacement,
    PresentationPolicyReceipt,
    PresentationPolicyResult,
    PresentationSeverity,
)
from daengs_place.place.presentation.needs import (
    INFORMATION_NEED_POLICY_ID,
    INFORMATION_NEED_POLICY_VERSION,
    InformationNeedId,
    InformationNeedSatisfaction,
    InformationNeedSpec,
    information_need_spec,
)
from daengs_place.place.source_facts.states import FactState

PRESENTATION_POLICY_ID = "place-presentation"
PRESENTATION_POLICY_VERSION = "1"

_CORE_ORDER: tuple[PresentationFactId, ...] = (
    PresentationFactId.PLACE_KIND,
    PresentationFactId.PLACE_DISTANCE,
    PresentationFactId.PLACE_ADDRESS,
    PresentationFactId.PET_ACCESS_ALLOWED,
    PresentationFactId.PET_RESTRICTIONS,
)
_CORE_INDEX = {fact_id: index for index, fact_id in enumerate(_CORE_ORDER)}
_CRITICAL_FACTS = {
    PresentationFactId.PET_ACCESS_ALLOWED,
    PresentationFactId.PET_SIZE,
    PresentationFactId.PET_RESTRICTIONS,
}
_DECISION_ROLE_LABELS = {
    DecisionRole.FILTERING: "필터",
    DecisionRole.RANKING: "정렬",
    DecisionRole.EVALUATION: "조건 평가",
}


def _placed(
    fact: PresentationFact,
    placement: PresentationPlacement,
    *,
    promoted_by: tuple[str, ...] = (),
) -> PresentationItem:
    return PresentationItem.model_validate(
        {
            **fact.model_dump(mode="python"),
            "placement": placement,
            "promoted_by": promoted_by,
        }
    )


def _fact_notices(fact: PresentationFact) -> tuple[PresentationNotice, ...]:
    notices = []
    if fact.evaluation_state is EvaluationState.INCOMPATIBLE:
        notices.append(
            PresentationNotice(
                code=f"{fact.fact_id.value}.incompatible",
                message=f"{fact.label}: 현재 반려견 조건과 맞지 않습니다.",
                severity=PresentationSeverity.CRITICAL,
                fact_ids=(fact.fact_id,),
            )
        )
    elif fact.evaluation_state is EvaluationState.CONDITIONAL:
        notices.append(
            PresentationNotice(
                code=f"{fact.fact_id.value}.conditional",
                message=f"{fact.label}: 추가 조건을 확인해야 합니다.",
                severity=PresentationSeverity.WARNING,
                fact_ids=(fact.fact_id,),
            )
        )
    if fact.fact_id in _CRITICAL_FACTS and fact.source_state in {
        FactState.NOT_FETCHED,
        FactState.NOT_PROVIDED,
        FactState.PARSE_FAILED,
        FactState.UNKNOWN,
    }:
        notices.append(
            PresentationNotice(
                code=f"{fact.fact_id.value}.{fact.source_state.value}",
                message=f"{fact.label}: {fact.display_text}",
                severity=PresentationSeverity.WARNING,
                fact_ids=(fact.fact_id,),
            )
        )
    link = fact.provenance.link
    consequential = tuple(
        role
        for role in fact.decision_roles
        if role in {DecisionRole.FILTERING, DecisionRole.RANKING, DecisionRole.EVALUATION}
    )
    if link is not None and link.state is LinkState.CANDIDATE and consequential:
        roles = "·".join(_DECISION_ROLE_LABELS[role] for role in consequential)
        notices.append(
            PresentationNotice(
                code=f"{fact.fact_id.value}.candidate_link_decision",
                message=(f"{fact.label}: 미검증 연결 후보의 정보가 {roles}에 사용됐습니다."),
                severity=PresentationSeverity.WARNING,
                fact_ids=(fact.fact_id,),
            )
        )
    return tuple(notices)


def _satisfies_need(spec: InformationNeedSpec, fact: PresentationFact) -> bool:
    if fact.source_state not in spec.required_states:
        return False
    if spec.satisfaction is InformationNeedSatisfaction.KNOWN:
        return True
    value = fact.value
    return value not in (None, "", [], {})


def arrange_presentation(
    facts: tuple[PresentationFact, ...],
    information_needs: tuple[InformationNeedId, ...] = (),
) -> PresentationPolicyResult:
    """한 effective fact를 한 summary placement에만 두고 정책 영수증을 남긴다."""

    fact_ids = [fact.fact_id for fact in facts]
    if len(set(fact_ids)) != len(fact_ids):
        raise ValueError("presentation policy requires one effective value per fact id")
    if len(set(information_needs)) != len(information_needs):
        raise ValueError("presentation information needs must be unique")

    specs = tuple(
        sorted(
            (information_need_spec(need_id) for need_id in information_needs),
            key=lambda spec: (-spec.priority, spec.need_id.value),
        )
    )
    promotions: dict[PresentationFactId, list[str]] = {}
    for spec in specs:
        for fact_id in spec.promoted_fact_ids:
            promotions.setdefault(fact_id, []).append(spec.need_id.value)

    core = []
    promoted = []
    detail = []
    notices: dict[str, PresentationNotice] = {}
    applied_rules = []
    by_id = {fact.fact_id: fact for fact in facts}

    for fact in facts:
        reasons = tuple(promotions.get(fact.fact_id, ()))
        if reasons:
            promoted.append(_placed(fact, PresentationPlacement.PROMOTED, promoted_by=reasons))
            applied_rules.extend(f"promote:{reason}" for reason in reasons)
        elif fact.fact_id in _CORE_INDEX:
            core.append(_placed(fact, PresentationPlacement.CORE))
            applied_rules.append(f"core:{fact.fact_id.value}")
        else:
            detail.append(_placed(fact, PresentationPlacement.DETAIL))
        for notice in _fact_notices(fact):
            notices.setdefault(notice.code, notice)
            applied_rules.append(f"notice:{notice.code}")

    for spec in specs:
        satisfying_ids = spec.satisfying_fact_ids or spec.promoted_fact_ids
        candidates = [by_id[fact_id] for fact_id in satisfying_ids if fact_id in by_id]
        if not any(_satisfies_need(spec, fact) for fact in candidates):
            notice = PresentationNotice(
                code=spec.fallback_notice_code,
                message=spec.fallback_notice,
                severity=(
                    PresentationSeverity.WARNING
                    if spec.safety.value in {"decision", "safety"}
                    else PresentationSeverity.INFO
                ),
                fact_ids=spec.promoted_fact_ids,
            )
            notices.setdefault(notice.code, notice)
            applied_rules.append(f"fallback:{spec.need_id.value}")
            # 요청에 맞춘 fallback이 같은 미상 상태를 더 구체적으로 설명한다. 일반 안전
            # 경고까지 함께 두면 한 사실이 두 경고처럼 보이므로 summary notice만 접는다.
            for fact in candidates:
                generic_code = f"{fact.fact_id.value}.{fact.source_state.value}"
                if notices.pop(generic_code, None) is not None:
                    applied_rules = [
                        rule for rule in applied_rules if rule != f"notice:{generic_code}"
                    ]
                    applied_rules.append(f"suppress:{generic_code}:by:{spec.fallback_notice_code}")

    promoted.sort(
        key=lambda item: (
            min(
                -information_need_spec(InformationNeedId(reason)).priority
                for reason in item.promoted_by
            ),
            item.fact_id.value,
        )
    )
    core.sort(key=lambda item: _CORE_INDEX[item.fact_id])
    detail.sort(key=lambda item: item.fact_id.value)
    ordered_notices = tuple(
        sorted(
            notices.values(),
            key=lambda notice: (
                {
                    PresentationSeverity.CRITICAL: 0,
                    PresentationSeverity.WARNING: 1,
                    PresentationSeverity.INFO: 2,
                }[notice.severity],
                notice.code,
            ),
        )
    )
    return PresentationPolicyResult(
        core_items=tuple(core),
        promoted_items=tuple(promoted),
        detail_items=tuple(detail),
        notices=ordered_notices,
        policy_receipt=PresentationPolicyReceipt(
            policy_id=PRESENTATION_POLICY_ID,
            policy_version=PRESENTATION_POLICY_VERSION,
            information_need_policy_id=INFORMATION_NEED_POLICY_ID,
            information_need_policy_version=INFORMATION_NEED_POLICY_VERSION,
            information_need_ids=tuple(spec.need_id.value for spec in specs),
            applied_rule_ids=tuple(dict.fromkeys(applied_rules)),
        ),
    )
