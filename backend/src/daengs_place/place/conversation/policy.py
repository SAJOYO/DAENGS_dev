"""Exclusive policy actions and a single revision-bound confirmation transaction."""

from dataclasses import dataclass, field
from datetime import timedelta
from uuid import uuid4

from daengs_place.place.conversation.compiler import compile_changes, fingerprint
from daengs_place.place.conversation.contract import PendingChange, TurnPlan
from daengs_place.place.conversation.intent import Interpretation
from daengs_place.place.conversation.render import ATTRIBUTES, confirmation
from daengs_place.place.filters.contract import FilterState, guard_filter_state

PENDING_SECONDS = 300
CLARIFICATIONS = {
    "conflicting_conditions": "서로 반대인 조건이 함께 있어요. 어느 조건을 적용할까요?",
    "missing_target": "어느 장소나 조건을 말씀하시는지 알려주세요.",
    "unsupported_goal": "이 요청은 아직 처리할 수 없어요. 찾을 업종이나 주차 조건을 알려주세요.",
    "ambiguous": "바꾸려는 조건을 조금 더 구체적으로 알려주세요.",
}

# A model label alone is insufficient authority to apply a pending transaction.
# Keep quotations, questions and additional conditions outside this bounded vocabulary.
PLAIN_CONSENT = frozenset(
    {
        "응",
        "ㅇㅇ",
        "네",
        "넵",
        "예",
        "좋아",
        "좋아요",
        "오케이",
        "ok",
        "확인",
        "동의",
        "해줘",
        "해주세요",
        "적용해줘",
        "적용해주세요",
        "그렇게해줘",
        "그렇게해주세요",
        "그래",
        "그래해줘",
        "진행해",
        "진행해줘",
        "진행해주세요",
        "좋아그렇게해줘",
        "네그렇게해주세요",
        "응그렇게해줘",
        "좋아진행해줘",
        "응적용해줘",
    }
)


def plain_consent(query):
    return "".join(query.lower().split()).replace(",", "").rstrip(".!~") in PLAIN_CONSENT


@dataclass(frozen=True)
class Decision:
    action: str
    plan: TurnPlan = field(default_factory=lambda: TurnPlan(goal="show"))
    candidate: FilterState | None = None
    pending: PendingChange | None = None
    question: str = ""
    code: str = ""
    intent: Interpretation | None = None


def base_revision(request):
    if request.base_revision is not None:
        return request.base_revision
    return request.previous.revision if request.previous else 0


async def decide(planner, request, now):
    old = request.previous
    pending = old.pending_proposal
    revise = False
    unverified_accept = False
    if pending is None and plain_consent(request.query):
        return Decision(
            "clarify",
            code="no_pending_proposal",
            question="지금은 적용을 기다리는 제안이 없어요. 원하는 조건을 알려주세요.",
        )
    if pending:
        valid = (
            pending.revision == base_revision(request)
            and pending.base_fingerprint == fingerprint(old.filters)
            and now < pending.expires_at
        )
        decision = await planner.decide_pending(request)
        if not valid and decision.decision not in {"new_request", "reject"}:
            # Do not interpret a bare consent as a fresh instruction after expiry.
            return Decision(
                "clarify",
                code="pending_expired",
                question="이전 제안이 만료되었어요. 원하는 조건을 다시 알려주세요.",
            )
        if decision.decision == "accept" and plain_consent(request.query):
            return Decision(
                "execute",
                candidate=guard_filter_state(pending.candidate),
                plan=TurnPlan(goal=pending.goal, refresh=pending.refresh),
                pending=pending,
            )
        unverified_accept = decision.decision == "accept"
        if decision.decision == "reject":
            return Decision(
                "reject",
                code="proposal_rejected",
                question="제안을 취소했어요. 현재 조건을 유지할게요.",
            )
        if decision.decision == "unclear":
            return Decision(
                "await_confirmation",
                pending=pending,
                code="confirmation_required",
                question=pending.question,
            )
        revise = decision.decision == "revise"
    context = request
    if revise:
        # Revision reinterprets only the user's correction against the saved candidate.
        context = request.model_copy(
            update={"previous": old.model_copy(update={"filters": pending.candidate})}
        )
    intent = await planner.plan(context)
    if not isinstance(intent, Interpretation):
        raise TypeError("expected semantic interpretation")
    if intent.feedback != "none":
        return Decision(
            "explain",
            code="feedback_no_mutation",
            question={
                "evaluation": "장소 평가는 찜이나 검색 조건에 자동 반영하지 않아요. 남기고 싶으면 찜해 달라고 말해 주세요.",
                "familiarity": "이미 아는 장소군요. 다른 후보를 보려면 더 보여 달라고 말해 주세요.",
                "information_dispute": "안내한 정보가 현장과 다를 수 있어요. 지금 자료만으로 이전이나 폐업 여부는 확인할 수 없어요.",
            }[intent.feedback],
            intent=intent,
        )
    if intent.search_scope == "bookmarks":
        return Decision("saved_search", intent=intent)
    if intent.spatial_scope != "keep":
        return Decision(
            "clarify",
            question="지역 제한 없는 검색은 찜 탭에서 할 수 있어요.",
            code="unbounded_requires_saved",
        )
    if intent.bookmark is not None:
        return Decision("bookmark", intent=intent)
    if intent.region_query:
        return Decision(
            "unsupported",
            intent=intent,
            code="region_change_unsupported",
            question="검색 지역 이동은 지도에서 할 수 있어요. 지도를 원하는 지역으로 옮긴 뒤 다시 검색해 주세요.",
        )
    if intent.unresolved != "none" or intent.goal == "clarify":
        return Decision(
            "clarify",
            intent=intent,
            code="clarification_required",
            question=CLARIFICATIONS.get(intent.unresolved, CLARIFICATIONS["ambiguous"]),
        )
    if (intent.browse != "current" or intent.place_edit) and (intent.unsupported or revise):
        return Decision(
            "clarify",
            code="exploration_needs_supported_request",
            question="장소 제외·다음 후보 요청과 확인 대기 조건을 한꺼번에 적용할 수 없어요. 먼저 적용할 요청을 알려주세요.",
            intent=intent,
        )
    plan = TurnPlan(
        goal=intent.goal, refresh=intent.refresh, reference_index=intent.reference_index
    )
    if intent.goal == "explain":
        # Explanation has no filter mutation authority, even if the model emits changes.
        return Decision("explain", plan=plan, candidate=old.filters, intent=intent)
    if unverified_accept:
        # A misclassified place question can still be explained. Other unverified
        # acceptances never execute or regenerate the saved proposal's conditions.
        return Decision(
            "await_confirmation",
            pending=pending,
            code="confirmation_required",
            question=pending.question + " 적용하려면 ‘적용해줘’라고 말씀해 주세요.",
        )
    candidate = compile_changes(context.previous.filters, intent.changes)
    unsupported = (
        tuple(dict.fromkeys((*pending.unsupported, *intent.unsupported)))
        if revise
        else intent.unsupported
    )
    if unsupported:
        if candidate == old.filters and intent.changes.model_dump(exclude_defaults=True) == {}:
            labels = "·".join(ATTRIBUTES[a] for a in unsupported)
            return Decision(
                "unsupported",
                intent=intent,
                code="unsupported_filters",
                question=f"{labels} 조건은 검색에 적용할 수 없어요. 원하는 업종이나 주차 조건을 알려주세요.",
            )
        if intent.reference_index is not None:
            return Decision(
                "clarify",
                intent=intent,
                code="reference_needs_confirmation",
                question="조건을 바꾸면 목록 순서가 달라져요. 먼저 검색 조건을 정해 주세요.",
            )
        question = confirmation(candidate, unsupported, plan.goal)
        proposal = PendingChange(
            id=uuid4(),
            revision=base_revision(request) + 1,
            base_fingerprint=fingerprint(old.filters),
            original_query=pending.original_query if revise else request.query,
            question=question,
            candidate=candidate,
            goal=plan.goal,
            refresh=plan.refresh,
            unsupported=unsupported,
            expires_at=now + timedelta(seconds=PENDING_SECONDS),
        )
        return Decision(
            "await_confirmation",
            plan=plan,
            candidate=candidate,
            pending=proposal,
            question=question,
            code="confirmation_required",
            intent=intent,
        )
    return Decision("execute", plan=plan, candidate=candidate, intent=intent)
