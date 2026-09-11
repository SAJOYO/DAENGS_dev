"""저장된 turn 에서 후보와 대기 되묻기를 뽑는 순수 함수들.

DB 가 필요 없다 — `ChatTurn` 을 손으로 만들어 넣는다.
"""

import uuid

from daengs_backend.models.chat import ChatTurn
from daengs_backend.orchestration.contracts import ObservationAxis
from daengs_backend.services.chat import candidates_of, pending_clarification_of


def _completed(user: str, assistant: str, *, status: str = "ANSWERED", clarify=None) -> ChatTurn:
    public: dict = {"status": status, "message": assistant}
    if clarify is not None:
        public["clarify"] = clarify
    return ChatTurn(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        client_message_id=uuid.uuid4(),
        processing_status="completed",
        user_content=user,
        assistant_content=assistant,
        assistant_status=status,
        public_response=public,
    )


def test_candidates_keep_order_and_carry_turn_ids() -> None:
    turns = [_completed("첫 질문", "첫 답"), _completed("둘째 질문", "둘째 답")]
    candidates = candidates_of(turns)
    assert [c.user for c in candidates] == ["첫 질문", "둘째 질문"]
    assert candidates[0].turn_id == turns[0].id


def test_candidates_of_reversed_input_is_reversed_output() -> None:
    """순서를 옮겨 싣기만 하는지 확인한다 — 뒤집어 넣으면 뒤집혀 나와야 한다.

    입력을 오래된 순으로 만들어 통과시키는 자리(레포지토리 쪽 반전)를 이 테스트가
    대신 검증하지는 못하지만, `candidates_of` 자신이 순서를 조용히 재정렬하지 않는다는
    것 — 즉 호출자가 준 순서를 그대로 믿고 옮긴다는 것 — 은 이 테스트가 잡는다.
    """
    oldest_first = [_completed("오래된 질문", "오래된 답"), _completed("최신 질문", "최신 답")]
    newest_first = list(reversed(oldest_first))
    assert [c.user for c in candidates_of(newest_first)] == ["최신 질문", "오래된 질문"]
    assert [c.user for c in candidates_of(oldest_first)] == ["오래된 질문", "최신 질문"]


def test_a_trailing_clarify_turn_is_the_pending_clarification() -> None:
    """스펙 ⑥(나) — 「미해결」은 가장 최근 완료 turn 이 CLARIFY 라는 뜻이다."""
    clarify = {
        "question": "식욕과 활력 중 어느 쪽이 달라 보이나요?",
        "missing": ["observation"],
        "missing_axes": ["APPETITE", "ENERGY"],
    }
    turns = [_completed("오늘 건강 어때?", "기록상 …", status="CLARIFY", clarify=clarify)]
    pending = pending_clarification_of(turns)
    assert pending is not None
    assert pending.turn_id == turns[0].id
    assert pending.missing_axes == [ObservationAxis.APPETITE, ObservationAxis.ENERGY]


def test_a_clarify_followed_by_another_turn_is_no_longer_pending() -> None:
    clarify = {"question": "어느 쪽인가요?", "missing": ["observation"], "missing_axes": []}
    turns = [
        _completed("오늘 건강 어때?", "기록상 …", status="CLARIFY", clarify=clarify),
        _completed("밥은 먹어", "그렇군요 …"),
    ]
    assert pending_clarification_of(turns) is None


def test_empty_missing_axes_means_axes_unknown_not_nothing_asked() -> None:
    """`ObservationAxis` docstring 이 #416 을 지목해 경고하는 자리."""
    clarify = {"question": "그거가 무엇을 가리키나요?", "missing": ["reference"], "missing_axes": []}
    turns = [_completed("그거 얼마나 자주 해?", "…", status="CLARIFY", clarify=clarify)]
    pending = pending_clarification_of(turns)
    assert pending is not None  # 되묻기는 분명히 있다
    assert pending.missing_axes == []  # 축만 모른다


def test_unknown_axis_value_is_dropped_not_crashed() -> None:
    """모델 열거형이 넓어지기 전의 옛 행 — 모르는 축 이름은 조용히 버려진다."""
    clarify = {
        "question": "어느 쪽인가요?",
        "missing": ["observation"],
        "missing_axes": ["APPETITE", "NOT_A_REAL_AXIS"],
    }
    turns = [_completed("오늘 어때?", "…", status="CLARIFY", clarify=clarify)]
    pending = pending_clarification_of(turns)
    assert pending is not None
    assert pending.missing_axes == [ObservationAxis.APPETITE]


def test_malformed_clarify_object_is_tolerated() -> None:
    """`clarify` 가 dict 가 아니거나 없어도 죽지 않고, 대기 없음으로 읽는다."""
    turns = [_completed("오늘 어때?", "…", status="CLARIFY", clarify="이건 문자열이다")]
    assert pending_clarification_of(turns) is None

    turns_missing_clarify = [_completed("오늘 어때?", "…", status="CLARIFY")]
    assert pending_clarification_of(turns_missing_clarify) is None


def test_no_turns_means_no_pending_and_no_candidates() -> None:
    assert candidates_of([]) == []
    assert pending_clarification_of([]) is None
