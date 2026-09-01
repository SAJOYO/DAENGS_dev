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
