"""Deterministic AssistantResponse aggregation from the approved truth table."""

from __future__ import annotations

from typing import Any

from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    AssistantStatus,
    CapabilityName,
    CapabilityResult,
    CapabilityStatus,
    ClarifyRequest,
    RoutePlan,
    RouteTrace,
    ScreeningHistory,
)
from daengs_backend.orchestration.redirects import NO_CAPABILITY_MESSAGE

_LABELS = {
    CapabilityName.TRAINING: "훈련",
    CapabilityName.LIFE: "생활 정보",
    CapabilityName.WALK: "산책",
    CapabilityName.PLACE: "장소",
    # 폴백은 전문 능력이 하나도 안 골렸을 때만 붙으므로(#279) 실제로는 단독 결과라
    # 이 라벨이 화면에 찍힐 일이 없다. 그래도 빠뜨리면 KeyError 다.
    CapabilityName.GENERAL: "일반",
    # 배타 실행이라 단독 결과여서 화면에 안 찍힌다. 그래도 빠뜨리면 KeyError 다 (GENERAL 과 같다).
    CapabilityName.VET_CONTACT: "응급",
    # 위 둘과 같다 — 쓰기는 승낙 한 번에 한 건이라 늘 단독 결과다 (#331 후속).
    CapabilityName.CARE_LOG: "케어 기록",
    # 배타 단일 요청이라 화면에 안 찍힌다. 그래도 빠뜨리면 KeyError 다 (D-078).
    CapabilityName.SKIN: "피부",
}

# HANDOFF 사용자 문구. `handoff.target`/`handoff.reason` 은 라우팅 내부 값이라
# (planner._HANDOFF_REASONS) message 에 그대로 섞으면 안 된다 — 구조화 필드는
# AssistantResponse.handoffs 로 이미 나가고 있으니 여기서는 사람이 읽을 문장만 짓는다.
_HANDOFF_MESSAGES = {
    "skin": "피부 사진을 등록해 함께 확인해 볼게요.",
    "gait": "보행 영상을 등록해 함께 확인해 볼게요.",
    # 위 둘과 달리 **의미 라우터가 못 고르는** target 이다 (`planner._HANDOFF_REASONS`).
    # "무엇을" 이나 "어느 아이" 를 확실히 모를 때 추측해서 쓰지 않고 사람이 적게 보내는
    # 자리이고, 쓰기 플래그가 꺼진 운영에서는 기록 의도가 전부 이 문장으로 끝난다.
    "care_log": "케어 기록 화면에서 남겨 드릴게요.",
}
# 아직 모르는 target 이 와도(v1 밖 확장) 내부 값을 노출하지 않는 안전한 문장.
_UNKNOWN_HANDOFF_MESSAGE = "추가 입력이 필요한 전용 기능으로 안내할게요."

#: 판정을 사용자 문장으로. HANDOFF·산책과 같은 이유로 내부 값을 그대로 안 쓴다 — `retake` 는
#: "판정 못 함"이지 "이상 없음"이 아니다.
#:
#: ⚠️ **`daengs_life.rag.stages.generate._VERDICT_KO` 의 사본이다.** 같은 판정이 프롬프트와
#: 답변에서 다른 말로 나오면 사용자가 그것을 다른 판정으로 읽는다. 두 벌인 것은 방향 때문이다 —
#: 여기서 `daengs_life` 를 import 하면 오케스트레이션이 도메인 어휘에 묶인다(D-035 의 반대 방향).
#: 사본끼리는 `tests/test_orchestration_aggregate.py` 가 대조한다 (#269 와 같은 장치).
_SCREENING_VERDICTS = {
    "normal": "특이 소견 없음",
    "abnormal": "이상 소견 있음",
    "retake": "사진으로 판정하지 못함",
}

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
    screening_history: ScreeningHistory | None = None,
) -> AssistantResponse:
    """Apply D-033/D-034 without using a model or rewriting domain messages.

    `include_route_trace` defaults off: the caller that knows the principal's permissions
    has to say yes (#238). A caller that forgets therefore leaks nothing.

    `screening_history` 는 같은 아이의 이전 판정들이다 (#79 3번). **능력이 답을 못 냈을 때만**
    절로 붙는다 — 아래 `_should_tell_history`. 기본값이 `None` 이라 안 넘긴 부르는 쪽은
    이 카드 이전과 같은 응답을 받는다.
    """
    route = _route_trace(route_plan) if include_route_trace else None
    asked = _general_ask(results)
    if asked is not None and route_plan.clarify is None:
        ask, grounded = asked
        # CLARIFY 의 **두 번째** 생산자 (D-068). 계획 시점 것(좌표 누락)이 먼저다.
        return AssistantResponse(
            request_id=request_id,
            status=AssistantStatus.CLARIFY,
            # 사용자가 보는 것은 이 한 칸이다 — 기록으로 말할 수 있는 것과 물을 것이 둘 다
            # 여기 있어야 한다. `clarify.question` 은 질문만 갖는다(되묻기를 따로 렌더하는
            # 클라이언트가 요약까지 질문 자리에 그리지 않게).
            message=_ask_message(ask.question, grounded),
            # 진리표의 "CLARIFY = 아무것도 실행되지 않았음" 을 클라이언트 쪽에서 그대로
            # 지킨다 — General 이 돌았다는 사실은 `route` 트레이스에만 남는다. 여기에
            # 결과를 실으면 프론트의 상태 설명과 `chat.categories_of` 가 같이 틀어진다.
            results=[],
            handoffs=[],
            clarify=ask,
            route=route,
        )
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
            # 라우터가 아무것도 못 고른 요청은 실무상 대부분 반려견과 무관한 요청이었다 —
            # 일반 답변 폴백 플래그가 꺼져 있을 때도(D-057) "무엇은 도울 수 있다" 를 말하는
            # 스코프드 리다이렉트를 쓴다 (#278). REFUSED 의 off_topic 과 같은 문장이다.
            status = AssistantStatus.FAILED
            message = NO_CAPABILITY_MESSAGE
        # 능력이 하나도 안 돈 자리다 — 이력이 사용자에게 닿는 **주된** 길이 여기다.
        # "지난번보다 어때요" 는 라우터가 skin 핸드오프만 내고 능력을 안 고르는 요청이다.
        message = _with_history(message, screening_history)
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
    if _should_tell_history(results):
        sections.insert(0, _history_message(screening_history))
    return AssistantResponse(
        request_id=request_id,
        status=top_status,
        message="\n\n".join(section for section in sections if section),
        results=results,
        handoffs=route_plan.handoffs,
        route=route,
    )


def _ask_message(question: str, grounded: str | None) -> str:
    """되묻기 한 턴의 사용자 문장 — 기록으로 말할 수 있는 것이 먼저, 물을 것이 나중.

    기록이 없으면 질문만 남는다. 빈 줄 두 칸을 앞에 붙이지 않으려고 이 자리를 함수로 뺀다.
    """
    return f"{grounded}\n\n{question}" if grounded else question


def _general_ask(results: list[CapabilityResult]) -> tuple[ClarifyRequest, str | None] | None:
    """General 이 **단독으로** 되물었을 때만 되묻기로 읽는다 (D-068).

    `RoutePlan.clarify` 는 안 건드린다 — 계획은 이미 굳었고, 배타성 불변식
    (`contracts.RoutePlan.clarify_is_exclusive` · `graph._validate_route_plan`)은 그대로
    서 있어야 한다. 되묻기가 사는 곳은 계획이 아니라 **집계**다.

    돌려주는 것은 (질문, 기록으로 먼저 말할 수 있는 것) 두 쪽이다. 뒤쪽은 없을 수 있다 —
    비로그인이거나 활성 강아지가 없으면 실을 기록 자체가 없다.

    단독 조건은 폴백 규칙(`planner.py`: 라우터가 아무것도 안 골랐을 때만 `general`)이
    이미 보장하지만, 여기서 한 번 더 건다 — 그 규칙이 풀리는 날 되묻기가 다른 능력의
    답을 조용히 삼키면 안 된다. 능력도 GENERAL 로 못 박는다: `data` 는 능력마다 모양이
    다른 자리라, 다른 능력이 `ask` 키를 쓰기 시작해도 대화 계약이 안 바뀐다.
    """
    if len(results) != 1:
        return None
    result = results[0]
    if result.capability is not CapabilityName.GENERAL:
        return None
    if result.status is not CapabilityStatus.OK:
        return None
    data = result.data or {}
    raw = data.get("ask")
    if raw is None:
        return None
    grounded = data.get("answer")
    # 어댑터가 이미 `ClarifyRequest` 로 조립해 검증한 값이다. 여기서 깨지면 우리 코드의
    # 버그이지 모델 출력 문제가 아니므로, 삼키지 않고 그대로 터뜨린다.
    return ClarifyRequest.model_validate(raw), grounded if isinstance(grounded, str) else None


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


def _should_tell_history(results: list[CapabilityResult]) -> bool:
    """이력 절을 붙일까. **능력이 답을 냈으면 안 붙인다.**

    물어본 것에 답이 있으면 이력은 안 물어본 이야기다 — "피부 치료비 지원 있어?" 에 약관을
    답해 놓고 지난 판정 셋을 덧붙이면 잡음이다. 답이 없을 때만(핸드오프뿐 · 전부 기권 ·
    전부 실패) "대신 아는 것" 으로 말한다.

    **라우터에게 묻지 않는다.** 여기서 모델이 판단하면 결정적 절 조립이 아니게 되고, O-9 가
    막은 2차 LLM 이 된다 (architecture.md 합성 단락).
    """
    return not any(result.status == CapabilityStatus.OK for result in results)


def _history_message(history: ScreeningHistory | None) -> str:
    """`[이전 기록]` 절. **판정과 경과일 말고는 담을 것이 없다** (불변식 15).

    ⚠️ **두 번째 문장이 이 절의 본체다.** 판정 셋을 나란히 놓으면 사람이 스스로 추세를
    읽는데, 그 차이는 매번 다른 사진에서 나온 것이라 몸이 달라졌다는 근거가 아니다
    (D-023 — 2단계 병변명 holdout 오답 56.6%, `stage1` 은 보정 전). 프롬프트에서 모델에게
    금지한 것을 화면에서 사용자에게도 말해 두지 않으면, 코드가 지킨 방어를 화면이 푼다.
    """
    if history is None or not history.entries:
        return ""
    items = " · ".join(
        f"{_when(entry.days_ago)} {_SCREENING_VERDICTS[entry.verdict]}"
        for entry in history.entries
    )
    return (
        f"[이전 기록] {items}\n"
        "사진이 매번 달라 기록만으로 좋아졌다·나빠졌다를 말할 수는 없어요. "
        "변화가 궁금하시면 진료를 받아보세요."
    )


def _with_history(message: str, history: ScreeningHistory | None) -> str:
    """이력 절을 **앞에** 붙인다 — 아는 것을 먼저 말하고 안내가 뒤에 온다."""
    clause = _history_message(history)
    return f"{clause}\n\n{message}" if clause else message


def _when(days_ago: int) -> str:
    """경과일을 말로. 날짜가 아닌 이유는 상류가 시계를 이미 풀었기 때문이다."""
    return "오늘" if days_ago == 0 else f"{days_ago}일 전"


def _handoff_message(route_plan: RoutePlan) -> str:
    return "\n".join(
        _HANDOFF_MESSAGES.get(handoff.target, _UNKNOWN_HANDOFF_MESSAGE)
        for handoff in route_plan.handoffs
    )


__all__ = ["aggregate_results"]
