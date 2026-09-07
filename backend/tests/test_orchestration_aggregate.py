"""HANDOFF 사용자 문구가 내부 라우팅 값을 노출하지 않는지, 빈 선택의 FAILED 문구 (aggregate.py)."""

from __future__ import annotations

from daengs_backend.orchestration.aggregate import aggregate_results
from daengs_backend.orchestration.contracts import (
    AssistantStatus,
    CapabilityName,
    CapabilityResult,
    CapabilityStatus,
    ErrorDetail,
    Handoff,
    OutcomeDetail,
    RoutePlan,
    RouterKind,
    ScreeningContext,
    ScreeningHistory,
)
from daengs_backend.orchestration.redirects import NO_CAPABILITY_MESSAGE

_LEAKED_CODES = ("skin", "gait", "image_upload_required", "video_upload_required")


def _plan(*, handoffs: list[Handoff]) -> RoutePlan:
    return RoutePlan(requests=[], handoffs=handoffs, router=RouterKind.DETERMINISTIC)


def _training_ok() -> CapabilityResult:
    return CapabilityResult(
        capability=CapabilityName.TRAINING,
        status=CapabilityStatus.OK,
        data={"answer": "훈련 답변"},
        elapsed_ms=1,
    )


def test_순수_gait_핸드오프는_구조는_보존하고_문구만_바꾼다() -> None:
    handoff = Handoff(target="gait", reason="video_upload_required")
    response = aggregate_results(request_id="r1", route_plan=_plan(handoffs=[handoff]), results=[])

    assert response.status == AssistantStatus.HANDOFF
    assert response.handoffs == [handoff]
    assert response.handoffs[0].target == "gait"
    assert response.handoffs[0].reason == "video_upload_required"
    assert "보행 영상" in response.message
    for code in _LEAKED_CODES:
        assert code not in response.message


def test_순수_skin_핸드오프는_구조는_보존하고_문구만_바꾼다() -> None:
    handoff = Handoff(target="skin", reason="image_upload_required")
    response = aggregate_results(request_id="r2", route_plan=_plan(handoffs=[handoff]), results=[])

    assert response.status == AssistantStatus.HANDOFF
    assert response.handoffs == [handoff]
    assert response.handoffs[0].target == "skin"
    assert response.handoffs[0].reason == "image_upload_required"
    assert "피부 사진" in response.message
    for code in _LEAKED_CODES:
        assert code not in response.message


def test_training_실행_결과에_gait_핸드오프_안내가_이어붙는다() -> None:
    handoff = Handoff(target="gait", reason="video_upload_required")
    response = aggregate_results(
        request_id="r3",
        route_plan=_plan(handoffs=[handoff]),
        results=[_training_ok()],
    )

    assert response.status == AssistantStatus.ANSWERED
    assert response.handoffs == [handoff]
    assert "훈련 답변" in response.message
    assert "보행 영상" in response.message
    for code in _LEAKED_CODES:
        assert code not in response.message


def test_모르는_핸드오프_대상은_일반_안내문으로_떨어진다() -> None:
    handoff = Handoff(target="future_capability", reason="something_new_required")
    response = aggregate_results(request_id="r4", route_plan=_plan(handoffs=[handoff]), results=[])

    assert response.status == AssistantStatus.HANDOFF
    assert response.handoffs == [handoff]
    assert "future_capability" not in response.message
    assert "something_new_required" not in response.message
    assert response.message  # 빈 문자열이 아니라 안전한 안내문이 있다


# ---------------------------------------------------------------------------
# 빈 선택 — 핸드오프도 결과도 없다 (#278)
#
# 라우터가 아무것도 못 고른 요청이다. 일반 답변 폴백 플래그가 꺼져 있어도(D-057) 지금
# 나가는 응답이라, "무엇은 도울 수 있다" 를 말하는 스코프드 리다이렉트를 쓴다 — REFUSED
# 의 off_topic 과 같은 문장이다 (daengs_backend.orchestration.redirects).
# ---------------------------------------------------------------------------


def test_빈_선택은_스코프드_리다이렉트_문구로_FAILED다() -> None:
    response = aggregate_results(request_id="r5", route_plan=_plan(handoffs=[]), results=[])

    assert response.status == AssistantStatus.FAILED
    assert response.message == NO_CAPABILITY_MESSAGE
    assert response.handoffs == []
    assert response.results == []


# ---------------------------------------------------------------------------
# 산책 판단 문구
#
# HANDOFF 와 같은 종류의 문제였다. `now.grade` 는 GOOD/CAUTION/UNSAFE 라는 **내부
# 값**인데 그걸 그대로 문장에 박아서, 사용자에게 "현재 산책 판단: CAUTION" 이 떴다.
# 구조화된 값은 results[].data 로 이미 나가고 있으므로 여기서는 사람이 읽을 문장만
# 짓는다 (파일 머리의 _HANDOFF_MESSAGES 주석과 같은 원칙).
# ---------------------------------------------------------------------------

_WALK_INTERNAL_CODES = ("GOOD", "CAUTION", "UNSAFE", "grade")


def _walk_ok(grade: str, *, axes: dict | None = None) -> CapabilityResult:
    now: dict = {"grade": grade}
    if axes is not None:
        now["axes"] = axes
    return CapabilityResult(
        capability=CapabilityName.WALK,
        status=CapabilityStatus.OK,
        data={"now": now},
        elapsed_ms=1,
    )


def _empty_plan() -> RoutePlan:
    return RoutePlan(requests=[], handoffs=[], router=RouterKind.DETERMINISTIC)


def test_산책_판단은_내부_값을_노출하지_않는다() -> None:
    for grade in ("GOOD", "CAUTION", "UNSAFE"):
        response = aggregate_results(
            request_id="r1", route_plan=_empty_plan(), results=[_walk_ok(grade)]
        )
        assert response.status == AssistantStatus.ANSWERED
        for code in _WALK_INTERNAL_CODES:
            assert code not in response.message, f"{grade}: {response.message}"


def test_등급마다_다른_문장이_나온다() -> None:
    messages = {
        grade: aggregate_results(
            request_id="r1", route_plan=_empty_plan(), results=[_walk_ok(grade)]
        ).message
        for grade in ("GOOD", "CAUTION", "UNSAFE")
    }
    assert len(set(messages.values())) == 3, messages
    assert all(message for message in messages.values())


def test_판단_근거가_있으면_같이_말한다() -> None:
    """`axes` 의 note 는 도메인이 쓴 사람 문장이라 그대로 이어 붙인다."""
    axes = {"heat": {"grade": "CAUTION", "note": "한낮 체감온도가 높습니다"}}
    response = aggregate_results(
        request_id="r1", route_plan=_empty_plan(), results=[_walk_ok("CAUTION", axes=axes)]
    )
    assert "한낮 체감온도가 높습니다" in response.message
    assert "CAUTION" not in response.message


def test_전체_등급과_다른_축의_근거는_안_붙인다() -> None:
    """왜 그 판단이 나왔는지를 말하는 자리다. 괜찮은 축까지 나열하면 흐려진다."""
    axes = {
        "heat": {"grade": "CAUTION", "note": "한낮 체감온도가 높습니다"},
        "air": {"grade": "GOOD", "note": "대기질은 좋습니다"},
    }
    response = aggregate_results(
        request_id="r1", route_plan=_empty_plan(), results=[_walk_ok("CAUTION", axes=axes)]
    )
    assert "한낮 체감온도가 높습니다" in response.message
    assert "대기질은 좋습니다" not in response.message


def test_근거가_없어도_판단은_말한다() -> None:
    response = aggregate_results(
        request_id="r1", route_plan=_empty_plan(), results=[_walk_ok("GOOD")]
    )
    assert response.message.strip()


def test_모르는_등급이_와도_내부_값이_안_샌다() -> None:
    """v1 밖 확장으로 새 등급이 생겨도 사용자에게 코드가 보이면 안 된다."""
    response = aggregate_results(
        request_id="r1", route_plan=_empty_plan(), results=[_walk_ok("SOMETHING_NEW")]
    )
    assert "SOMETHING_NEW" not in response.message
    assert response.message.strip()


# ---------------------------------------------------------------- 이력 절 (#79 3번)
#
# `context["screening_history"]` 는 Life 프롬프트까지 가지만, Life 는 근거 기반 RAG 라
# 조례·약관만 답합니다 — 사용자에게 이력을 **알려주는** 통로가 아닙니다 (2026-09-07 실측:
# "예전에 피부 찍어둔 기록 있었나?" 에 수의사법 제13조로 답했습니다). 그 일은 여기,
# 결정적 절 조립이 합니다 (O-9 · architecture.md 합성 단락).
#
# 이 절이 지키는 것은 둘입니다. **① 답이 있을 때는 침묵하는가** — 물어본 것에 답이 있으면
# 이력은 안 물어본 이야기입니다. **② 견주지 말라를 사용자에게도 말하는가** — 판정 셋을
# 나란히 놓으면 사람이 스스로 추세를 읽는데, 그 차이는 몸이 달라졌다는 근거가 아닙니다
# (D-023). 프롬프트에서 모델에게만 금지하면 코드가 지킨 방어를 화면이 풉니다.

_HISTORY = ScreeningHistory(
    entries=[
        ScreeningContext(verdict="abnormal", days_ago=30),
        ScreeningContext(verdict="normal", days_ago=60),
        ScreeningContext(verdict="retake", days_ago=90),
    ]
)


def _skin_plan() -> RoutePlan:
    return _plan(handoffs=[Handoff(target="skin", reason="image_upload_required")])


def _life_failed() -> CapabilityResult:
    return CapabilityResult(
        capability=CapabilityName.LIFE,
        status=CapabilityStatus.ERROR,
        error=ErrorDetail(kind="TimeoutError", detail="생활 정보 기능 실행에 실패했습니다."),
        elapsed_ms=1,
    )


def _life_abstained() -> CapabilityResult:
    return CapabilityResult(
        capability=CapabilityName.LIFE,
        status=CapabilityStatus.ABSTAINED,
        abstention=OutcomeDetail(code="ungrounded", message="자료에서 근거를 찾지 못했습니다."),
        elapsed_ms=1,
    )


def _life_ok() -> CapabilityResult:
    return CapabilityResult(
        capability=CapabilityName.LIFE,
        status=CapabilityStatus.OK,
        data={"answer": "피부병 치료비를 보장하는 특별약관이 있습니다[1]."},
        elapsed_ms=1,
    )


# ---------------------------------------------------------------- 붙는 조건


def test_능력이_답을_냈으면_이력을_말하지_않는다() -> None:
    """물어본 것에 답이 있으면 이력은 안 물어본 이야기입니다 — "치료비 지원 있어?" 에
    약관을 답해 놓고 지난 판정 셋을 덧붙이면 잡음입니다."""
    response = aggregate_results(
        request_id="h1",
        route_plan=_empty_plan(),
        results=[_life_ok()],
        screening_history=_HISTORY,
    )
    assert "[이전 기록]" not in response.message
    assert response.message == "피부병 치료비를 보장하는 특별약관이 있습니다[1]."


def test_핸드오프뿐이면_이력을_먼저_말한다() -> None:
    """**이 카드의 주된 자리입니다.** "지난번보다 어때요" 는 라우터가 skin 핸드오프만 내고
    능력을 하나도 안 고르는 요청이라, 이력이 사용자에게 닿는 유일한 길이 여기입니다."""
    response = aggregate_results(
        request_id="h2", route_plan=_skin_plan(), results=[], screening_history=_HISTORY
    )
    assert response.status == AssistantStatus.HANDOFF
    assert response.message.startswith("[이전 기록] ")
    assert "피부 사진을 등록해" in response.message
    assert response.message.index("[이전 기록]") < response.message.index("피부 사진")


def test_아무_능력도_못_골랐을_때도_말한다() -> None:
    response = aggregate_results(
        request_id="h3", route_plan=_empty_plan(), results=[], screening_history=_HISTORY
    )
    assert response.status == AssistantStatus.FAILED
    assert "[이전 기록]" in response.message
    assert NO_CAPABILITY_MESSAGE in response.message


def test_능력이_실패하거나_기권했으면_말한다() -> None:
    """답이 없을 때 "대신 아는 것" 으로 말하는 자리입니다."""
    for result in (_life_failed(), _life_abstained()):
        response = aggregate_results(
            request_id="h4",
            route_plan=_empty_plan(),
            results=[result],
            screening_history=_HISTORY,
        )
        assert response.message.startswith("[이전 기록] "), result.status


def test_일부만_성공해도_말하지_않는다() -> None:
    """PARTIAL 은 답이 나온 것입니다 — 하나라도 OK 면 침묵합니다."""
    response = aggregate_results(
        request_id="h5",
        route_plan=_empty_plan(),
        results=[_life_ok(), _life_failed()],
        screening_history=_HISTORY,
    )
    assert "[이전 기록]" not in response.message


def test_CLARIFY_에는_안_붙는다() -> None:
    """CLARIFY 는 배타적입니다 (계약 §2) — 되묻는 자리에 다른 이야기를 얹지 않습니다."""
    from daengs_backend.orchestration.contracts import ClarifyRequest

    plan = RoutePlan(
        requests=[],
        handoffs=[],
        clarify=ClarifyRequest(question="어느 아이 이야기인가요?", missing=["active_dog_id"]),
        router=RouterKind.DETERMINISTIC,
    )
    response = aggregate_results(
        request_id="h6", route_plan=plan, results=[], screening_history=_HISTORY
    )
    assert response.status == AssistantStatus.CLARIFY
    assert response.message == "어느 아이 이야기인가요?"


def test_이력이_없으면_이_카드_이전과_같다() -> None:
    """기본값이 `None` 이라, 안 넘긴 부르는 쪽은 아무것도 달라지지 않습니다."""
    base = aggregate_results(request_id="h7", route_plan=_skin_plan(), results=[])
    for history in (None, ScreeningHistory(entries=[])):
        same = aggregate_results(
            request_id="h7", route_plan=_skin_plan(), results=[], screening_history=history
        )
        assert same.message == base.message


# ---------------------------------------------------------------- 절의 내용


def test_이력_절은_판정과_경과일만_말한다() -> None:
    response = aggregate_results(
        request_id="h8", route_plan=_skin_plan(), results=[], screening_history=_HISTORY
    )
    line = response.message.splitlines()[0]
    assert line == (
        "[이전 기록] 30일 전 이상 소견 있음 · 60일 전 특이 소견 없음 · "
        "90일 전 사진으로 판정하지 못함"
    )
    for 금지 in ("abnormal", "normal", "retake", "구진", "융기", "%", "0."):
        assert 금지 not in line


def test_오늘_찍은_기록은_날짜를_안_센다() -> None:
    response = aggregate_results(
        request_id="h9",
        route_plan=_skin_plan(),
        results=[],
        screening_history=ScreeningHistory(
            entries=[ScreeningContext(verdict="normal", days_ago=0)]
        ),
    )
    assert "[이전 기록] 오늘 특이 소견 없음" in response.message


def test_이력_절이_비교를_막는_문장을_들고_있다() -> None:
    """**이 절의 본체입니다.** 판정 셋을 나란히 놓으면 사람이 스스로 추세를 읽는데, 그
    차이는 매번 다른 사진에서 나온 것이라 몸이 달라졌다는 근거가 아닙니다 (D-023).
    프롬프트에서 모델에게만 금지하면 코드가 지킨 방어를 화면이 풉니다."""
    response = aggregate_results(
        request_id="h10", route_plan=_skin_plan(), results=[], screening_history=_HISTORY
    )
    assert "좋아졌다·나빠졌다를 말할 수는 없어요" in response.message
    assert "진료를 받아보세요" in response.message


def test_판정_어휘가_프롬프트와_같은_말이다() -> None:
    """같은 판정이 프롬프트와 답변에서 다른 말로 나오면 사용자가 다른 판정으로 읽습니다.

    사본이 두 벌인 것은 방향 때문입니다 — `aggregate` 가 `daengs_life` 를 import 하면
    오케스트레이션이 도메인 어휘에 묶입니다(D-035 의 반대 방향). 그래서 여기서 대조합니다
    (능력 이름 사본 셋을 대조하는 #269 와 같은 장치)."""
    from daengs_backend.orchestration import aggregate
    from daengs_life.rag.stages.generate import _VERDICT_KO

    assert aggregate._SCREENING_VERDICTS == _VERDICT_KO
