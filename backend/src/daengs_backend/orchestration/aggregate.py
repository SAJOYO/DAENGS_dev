"""Deterministic AssistantResponse aggregation from the approved truth table."""

from __future__ import annotations

from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    AssistantStatus,
    CapabilityName,
    CapabilityResult,
    CapabilityStatus,
    RoutePlan,
)

_LABELS = {
    CapabilityName.TRAINING: "훈련",
    CapabilityName.LIFE: "생활 정보",
    CapabilityName.WALK: "산책",
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


def aggregate_results(
    *, request_id: str, route_plan: RoutePlan, results: list[CapabilityResult]
) -> AssistantResponse:
    """Apply D-033/D-034 without using a model or rewriting domain messages."""
    if route_plan.clarify is not None:
        return AssistantResponse(
            request_id=request_id,
            status=AssistantStatus.CLARIFY,
            message=route_plan.clarify.question,
            results=[],
            handoffs=[],
            clarify=route_plan.clarify,
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
    )


def _result_message(result: CapabilityResult, *, multiple: bool) -> str:
    message = ""
    if result.status == CapabilityStatus.OK and result.data:
        answer = result.data.get("answer")
        if isinstance(answer, str):
            message = answer
        elif result.capability == CapabilityName.WALK:
            now = result.data.get("now")
            grade = now.get("grade") if isinstance(now, dict) else None
            message = f"현재 산책 판단: {grade}" if grade else "산책 판단을 완료했습니다."
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


def _handoff_message(route_plan: RoutePlan) -> str:
    return "\n".join(
        _HANDOFF_MESSAGES.get(handoff.target, _UNKNOWN_HANDOFF_MESSAGE)
        for handoff in route_plan.handoffs
    )


__all__ = ["aggregate_results"]
