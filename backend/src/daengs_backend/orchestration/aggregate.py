"""Deterministic AssistantResponse aggregation from the approved truth table."""

from __future__ import annotations

from typing import Any

from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    AssistantStatus,
    CapabilityName,
    CapabilityResult,
    CapabilityStatus,
    RoutePlan,
    RouteTrace,
)

_LABELS = {
    CapabilityName.TRAINING: "훈련",
    CapabilityName.LIFE: "생활 정보",
    CapabilityName.WALK: "산책",
    CapabilityName.PLACE: "장소",
}

# HANDOFF 사용자 문구. `handoff.target`/`handoff.reason` 은 라우팅 내부 값이라
# (planner._HANDOFF_REASONS) message 에 그대로 섞으면 안 된다 — 구조화 필드는
# AssistantResponse.handoffs 로 이미 나가고 있으니 여기서는 사람이 읽을 문장만 짓는다.
_HANDOFF_MESSAGES = {
    "skin": "피부 사진을 등록해 함께 확인해 볼게요.",
    "gait": "보행 영상을 등록해 함께 확인해 볼게요.",
}
# 아직 모르는 target 이 와도(v1 밖 확장) 내부 값을 노출하지 않는 안전한 문장.
_UNKNOWN_HANDOFF_MESSAGE = "추가 입력이 필요한 전용 기능으로 안내할게요."

# 산책 판단 사용자 문구. HANDOFF 와 같은 이유다 — `now.grade` 는 GOOD/CAUTION/UNSAFE
# 라는 내부 값이고, 구조화된 값은 results[].data.now 로 이미 나가고 있다. 예전에는
# f"현재 산책 판단: {grade}" 였어서 앱에 "현재 산책 판단: CAUTION" 이 그대로 떴다.
_WALK_VERDICTS = {
    "GOOD": "지금 산책하기 좋아요.",
    "CAUTION": "지금 나가도 되지만 조심하는 게 좋아요.",
    "UNSAFE": "지금은 산책을 미루는 게 좋겠어요.",
}
# 새 등급이 생겨도 코드가 새지 않게. 판단은 났으므로 "모르겠다" 와는 다르다
# (그건 ABSTAINED 로 온다).
_UNKNOWN_WALK_VERDICT = "지금 산책 조건을 확인했어요."


def aggregate_results(
    *,
    request_id: str,
    route_plan: RoutePlan,
    results: list[CapabilityResult],
    include_route_trace: bool = False,
) -> AssistantResponse:
    """Apply D-033/D-034 without using a model or rewriting domain messages.

    `include_route_trace` defaults off: the caller that knows the principal's permissions
    has to say yes (#238). A caller that forgets therefore leaks nothing.
    """
    route = _route_trace(route_plan) if include_route_trace else None
    if route_plan.clarify is not None:
        return AssistantResponse(
            request_id=request_id,
            status=AssistantStatus.CLARIFY,
            message=route_plan.clarify.question,
            results=[],
            handoffs=[],
            clarify=route_plan.clarify,
            route=route,
        )

    if not results:
        if route_plan.handoffs:
            status = AssistantStatus.HANDOFF
            message = _handoff_message(route_plan)
        else:
            status = AssistantStatus.FAILED
            message = "실행하거나 안내할 수 있는 기능이 없습니다."
        return AssistantResponse(
            request_id=request_id,
            status=status,
            message=message,
            results=[],
            handoffs=route_plan.handoffs,
            route=route,
        )

    statuses = [result.status for result in results]
    if CapabilityStatus.OK in statuses:
        top_status = (
            AssistantStatus.ANSWERED
            if all(status == CapabilityStatus.OK for status in statuses)
            else AssistantStatus.PARTIAL
        )
    elif CapabilityStatus.REFUSED in statuses:
        top_status = AssistantStatus.REFUSED
    elif all(status == CapabilityStatus.ABSTAINED for status in statuses):
        top_status = AssistantStatus.UNCERTAIN
    elif all(status == CapabilityStatus.PENDING for status in statuses):
        top_status = AssistantStatus.PENDING
    else:
        # ERROR/TIMEOUT-only is FAILED. Other no-OK/no-REFUSED PENDING mixtures
        # are not emitted by Card 1 capabilities and remain a future contract decision.
        top_status = AssistantStatus.FAILED

    sections = [_result_message(result, multiple=len(results) > 1) for result in results]
    if route_plan.handoffs:
        sections.append(_handoff_message(route_plan))
    return AssistantResponse(
        request_id=request_id,
        status=top_status,
        message="\n\n".join(section for section in sections if section),
        results=results,
        handoffs=route_plan.handoffs,
        route=route,
    )


def _route_trace(route_plan: RoutePlan) -> RouteTrace:
    """Copy the plan's observation metadata across — nothing is derived or looked up here."""
    return RouteTrace(
        router=route_plan.router,
        model=route_plan.model,
        prompt_version=route_plan.prompt_version,
    )


def _result_message(result: CapabilityResult, *, multiple: bool) -> str:
    message = ""
    if result.status == CapabilityStatus.OK and result.data:
        answer = result.data.get("answer")
        if isinstance(answer, str):
            message = answer
        elif result.capability == CapabilityName.WALK:
            message = _walk_message(result.data.get("now"))
    elif result.status == CapabilityStatus.ABSTAINED and result.abstention:
        message = result.abstention.message
    elif result.status == CapabilityStatus.REFUSED and result.refusal:
        message = result.refusal.message
    elif result.status == CapabilityStatus.PENDING and result.job:
        message = f"처리가 진행 중입니다. 상태 확인: {result.job.poll}"
    elif result.error:
        message = result.error.detail

    if multiple:
        return f"[{_LABELS[result.capability]}]\n{message}"
    return message


def _walk_message(now: Any) -> str:
    """산책 판단을 사람이 읽는 문장으로.

    등급은 내부 값이라 그대로 쓰지 않고 [_WALK_VERDICTS] 를 거친다. 판단이 왜
    그렇게 났는지는 도메인이 축(`axes`)마다 사람 문장으로 적어 두므로 **그 문장을
    그대로** 이어 붙인다 — 여기서 새로 짓지 않는다(이 모듈은 도메인 문장을 다시
    쓰지 않는다는 것이 원칙이다).

    **전체 등급과 같은 축의 근거만** 붙인다. 왜 그 판단이 나왔는지를 말하는
    자리라, 괜찮은 축까지 나열하면 무엇 때문에 조심하라는 것인지 흐려진다.
    """
    if not isinstance(now, dict):
        return _UNKNOWN_WALK_VERDICT
    grade = now.get("grade")
    verdict = _WALK_VERDICTS.get(grade, _UNKNOWN_WALK_VERDICT)

    axes = now.get("axes")
    if not isinstance(axes, dict):
        return verdict
    reasons = [
        axis["note"]
        for axis in axes.values()
        if isinstance(axis, dict)
        and axis.get("grade") == grade
        and isinstance(axis.get("note"), str)
        and axis["note"].strip()
    ]
    if not reasons:
        return verdict
    return verdict + " " + " ".join(reasons)


def _handoff_message(route_plan: RoutePlan) -> str:
    return "\n".join(
        _HANDOFF_MESSAGES.get(handoff.target, _UNKNOWN_HANDOFF_MESSAGE)
        for handoff in route_plan.handoffs
    )


__all__ = ["aggregate_results"]
