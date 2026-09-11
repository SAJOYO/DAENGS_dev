import uuid

import pytest

from daengs_backend.orchestration.contracts import ObservationAxis
from daengs_backend.orchestration.resolver import (
    PendingClarification,
    PriorTurn,
    ResolvedTurn,
    TurnRelation,
    needs_resolution,
    new_turn,
)


def _turn(user: str, assistant: str) -> PriorTurn:
    return PriorTurn(turn_id=uuid.uuid4(), user=user, assistant=assistant)


def test_no_candidates_needs_no_resolution() -> None:
    """수용 케이스 1 — 이력 없는 독립 질문은 모델을 안 태운다."""
    assert needs_resolution(query="강아지 사료 추천해줘", candidates=(), pending=None) is False


def test_unrelated_old_history_needs_no_resolution() -> None:
    """수용 케이스 7 — 오래된 무관한 이력은 현재 라우팅을 안 건드린다."""
    candidates = [_turn("산책 코스 추천해줘", "근처 공원을 추천합니다.")]
    assert (
        needs_resolution(query="심장사상충 예방약 얼마나 자주 먹여?", candidates=candidates, pending=None)
        is False
    )


@pytest.mark.parametrize(
    "query",
    [
        "그거 얼마나 자주 해?",
        "아까 말한 거 다시 설명해줘",
        "아니 산책 말고 밥",
        "그러니까 발을 저는 이유가 뭐냐고",
        "그니까 그걸 네가 물어봐야지",
    ],
)
def test_context_dependent_markers_need_resolution(query: str) -> None:
    candidates = [_turn("사료 추천해줘", "저알레르기 사료를 고려해 보세요.")]
    assert needs_resolution(query=query, candidates=candidates, pending=None) is True


def test_pending_clarification_always_needs_resolution() -> None:
    """수용 케이스 5 의 앞 절반 — 되묻기가 대기 중이면 표지가 없어도 이어야 한다."""
    pending = PendingClarification(
        turn_id=uuid.uuid4(),
        question="식욕과 활력 중 어느 쪽이 달라 보이나요?",
        missing_axes=[ObservationAxis.APPETITE, ObservationAxis.ENERGY],
    )
    assert needs_resolution(query="밥은 먹는데 계속 누워 있어", candidates=(), pending=pending) is True


def test_context_ask_is_not_an_observation_ask() -> None:
    """PR 본문 ④ — 축이 비면 후속 답변을 축에 묶지 않는다."""
    context_ask = PendingClarification(
        turn_id=uuid.uuid4(),
        question="이전 대화에서 어떤 내용인지 확인하기 어렵습니다.",
        missing=["observation"],
        missing_axes=[],
    )
    observation_ask = PendingClarification(
        turn_id=uuid.uuid4(),
        question="식욕과 활력 중 어느 쪽이 달라 보이나요?",
        missing=["observation"],
        missing_axes=[ObservationAxis.APPETITE],
    )
    # `missing` 은 둘 다 같다 — 가르는 것은 축뿐이다.
    assert context_ask.missing == observation_ask.missing
    assert context_ask.is_observation_ask is False
    assert observation_ask.is_observation_ask is True


def test_new_turn_carries_the_query_verbatim_and_nothing_else() -> None:
    resolved = new_turn("강아지 사료 추천해줘")
    assert resolved.relation is TurnRelation.NEW
    assert resolved.current_query == "강아지 사료 추천해줘"
    assert resolved.referenced_turn_id is None
    assert resolved.pending_clarification_id is None
    assert resolved.standalone_query is None
    assert resolved.context_used == []


def test_resolved_turn_rejects_two_anchors() -> None:
    """referenced_turn_id 와 pending_clarification_id 는 둘 중 하나만."""
    with pytest.raises(ValueError):
        ResolvedTurn(
            relation=TurnRelation.FOLLOW_UP,
            current_query="그거 얼마나 자주 해?",
            referenced_turn_id=uuid.uuid4(),
            pending_clarification_id=uuid.uuid4(),
            resolution_confidence=0.9,
        )
