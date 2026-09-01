"""HANDOFF 사용자 문구가 내부 라우팅 값을 노출하지 않는지 (aggregate.py)."""

from __future__ import annotations

from daengs_backend.orchestration.aggregate import aggregate_results
from daengs_backend.orchestration.contracts import (
    AssistantStatus,
    CapabilityName,
    CapabilityResult,
    CapabilityStatus,
    Handoff,
    RoutePlan,
    RouterKind,
)

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
