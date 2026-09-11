import uuid

import pytest

from daengs_backend.orchestration.contracts import ObservationAxis
from daengs_backend.orchestration.resolver import (
    MAX_ASSISTANT_CHARS,
    TURN_RESOLVER_PROMPT_VERSION,
    PendingClarification,
    PriorTurn,
    ResolvedTurn,
    TurnRelation,
    build_candidate_block,
    build_turn_resolver_prompt,
    fit_candidates,
    needs_resolution,
    new_turn,
    truncate_assistant,
    validate_resolved_turn,
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


@pytest.mark.parametrize(
    "query",
    [
        "말고기 먹여도 되나요",
        "또띠아 먹여도 돼?",
        "강아지가 또 토했어요",
    ],
)
def test_false_positive_substrings_do_not_need_resolution(query: str) -> None:
    """fix round 1 — `말고기` 의 `말고`, `또띠아`/"또 토했어" 의 `또` 는 새 주제 문장에
    박힌 부분 문자열일 뿐 앞 turn 을 가리키는 표지가 아니다. 후보가 있어도(=short-circuit
    없이 정규식이 실제로 돌아도) 걸리면 안 된다."""
    candidates = [_turn("사료 추천해줘", "저알레르기 사료를 고려해 보세요.")]
    assert needs_resolution(query=query, candidates=candidates, pending=None) is False


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


def test_conversation_context_builds_without_importing_resolver() -> None:
    """`ConversationContext` 와 `TurnRelation` 은 `contracts.py` 소유다 (#416) —
    `resolver` 를 임포트하지 않고 `contracts` 만으로도 완전히 조립돼야 한다. 과거에는
    `TurnRelation` 이 `resolver.py` 에 있었고 `ConversationContext` 가 `TYPE_CHECKING`
    forward reference 로 그것을 참조했는데, `resolver` 를 안 거치면 pydantic 이 모델을
    "미완성" 으로 두고 인스턴스화에서 조용히 실패하는 함정이 있었다. 이 테스트는 그
    함정이 없다는 것을 증명한다."""
    from daengs_backend.orchestration.contracts import ConversationContext as CC
    from daengs_backend.orchestration.contracts import TurnRelation as TR

    context = CC(relation=TR.NEW)
    assert context.relation is TR.NEW


def test_assistant_text_is_truncated_with_an_ellipsis() -> None:
    long = "가" * 500
    out = truncate_assistant(long)
    assert len(out) == MAX_ASSISTANT_CHARS + 1
    assert out.endswith("…")


def test_short_assistant_text_is_untouched() -> None:
    assert truncate_assistant("네, 맞습니다.") == "네, 맞습니다."


def test_oldest_pairs_are_dropped_first_when_the_block_is_too_long() -> None:
    turns = [_turn("질" + "문" * 1_500, "답" * 400) for _ in range(3)]
    fitted = fit_candidates(turns)
    assert len(fitted) < 3
    assert fitted[-1] is turns[-1]  # 최신 쌍은 무조건 남는다


def test_the_newest_pair_always_survives() -> None:
    """한 쌍의 최대치(2,000+400)가 블록 상한 아래라 이 규칙은 늘 만족 가능하다."""
    turns = [_turn("질" * 2_000, "답" * 400)]
    assert fit_candidates(turns) == turns


def test_newest_pairs_up_to_max_candidate_pairs_survive() -> None:
    """pair count 상한을 초과하면 오래된 쌍부터 버린다. 5개를 주고 3개가 남아야 하고,
    그 3개는 가장 최신의 3개여야 한다."""
    from daengs_backend.orchestration.resolver import MAX_CANDIDATE_PAIRS

    # 5개의 짧은 쌍 — 문자 예산은 충분하므로 pair count 상한이 결정한다
    turns = [_turn(f"질문{i}", f"답변{i}") for i in range(5)]
    fitted = fit_candidates(turns)

    assert len(fitted) == MAX_CANDIDATE_PAIRS
    # 가장 최신의 3개여야 한다 (index 2, 3, 4)
    assert fitted == turns[2:5]
    # 오래된 것부터 유지해야 한다 (oldest-first order)
    assert fitted[0] is turns[2]
    assert fitted[1] is turns[3]
    assert fitted[2] is turns[4]


def test_block_numbers_pairs_oldest_first_for_reference() -> None:
    turns = [_turn("첫 질문", "첫 답"), _turn("둘째 질문", "둘째 답")]
    block = build_candidate_block(turns)
    assert "U1: 첫 질문" in block
    assert "A1: 첫 답" in block
    assert "U2: 둘째 질문" in block
    assert block.index("U1:") < block.index("U2:")


def test_empty_candidates_render_to_an_empty_block() -> None:
    assert build_candidate_block([]) == ""


def test_current_query_is_last_in_the_prompt() -> None:
    """Place 실측(2026-09-10): 문맥 뒤에 최신 질의를 두면 최신 요청을 놓치는 퇴행이 사라진다."""
    turns = [_turn("사료 추천해줘", "저알레르기 사료를 고려해 보세요.")]
    prompt = build_turn_resolver_prompt(query="그거 얼마나 자주 해?", candidates=turns, pending=None)
    assert prompt.index("CANDIDATE_TURNS:") < prompt.index("CURRENT_QUERY:")
    assert prompt.rstrip().endswith("CURRENT_QUERY: 그거 얼마나 자주 해?")
    assert TURN_RESOLVER_PROMPT_VERSION in prompt


def test_pending_clarification_block_carries_axes_as_asked_not_observed() -> None:
    pending = PendingClarification(
        turn_id=uuid.uuid4(),
        question="식욕과 활력 중 어느 쪽이 달라 보이나요?",
        missing_axes=[ObservationAxis.APPETITE],
    )
    prompt = build_turn_resolver_prompt(query="밥은 먹는데 계속 누워 있어", candidates=(), pending=pending)
    assert "PENDING_CLARIFICATION:" in prompt
    marker = prompt.index("PENDING_CLARIFICATION:")
    block = prompt[marker:]
    assert "APPETITE" in block
    # 물은 항목이지 관찰된 사실이 아니라는 것을 렌더된 블록 자체가 키 이름으로 말한다
    # (R10 — `_POLICY` 에도 "asked" 가 나오므로 블록 밖에서 찾으면 항상 통과해 버린다).
    assert "asked_axes" in block


def test_model_may_not_reference_a_turn_outside_the_candidates() -> None:
    turns = [_turn("사료 추천해줘", "저알레르기 사료를 고려해 보세요.")]
    raw = {
        "relation": "FOLLOW_UP",
        "referenced_index": 9,
        "standalone_query": "사료를 얼마나 자주 줘?",
        "resolution_confidence": 0.9,
    }
    assert validate_resolved_turn(raw, query="그거 얼마나 자주 해?", candidates=turns, pending=None) is None


def test_referenced_index_becomes_the_real_turn_id() -> None:
    turns = [_turn("사료 추천해줘", "저알레르기 사료를 고려해 보세요.")]
    raw = {
        "relation": "FOLLOW_UP",
        "referenced_index": 1,
        "standalone_query": "사료를 얼마나 자주 줘?",
        "resolution_confidence": 0.9,
    }
    resolved = validate_resolved_turn(
        raw, query="그거 얼마나 자주 해?", candidates=turns, pending=None
    )
    assert resolved is not None
    assert resolved.referenced_turn_id == turns[0].turn_id
    assert resolved.referenced_original_request == "사료 추천해줘"
    assert resolved.context_used == [turns[0].turn_id]
    # 원문은 모델이 만든 표현으로 대체되지 않는다.
    assert resolved.current_query == "그거 얼마나 자주 해?"


def test_malformed_output_is_rejected_without_surfacing_it() -> None:
    assert validate_resolved_turn("not json", query="아무 말", candidates=(), pending=None) is None


def test_new_relation_with_a_referenced_index_is_rejected() -> None:
    """Finding 2 / R9 — NEW 인데 후보를 같이 지목하는 것은 모순된 출력이다.

    인덱스를 조용히 버리지 않고 결과 자체를 신뢰하지 않는다 — 범위 밖 인덱스를
    거부하는 것과 같은 취급이다.
    """
    turns = [_turn("사료 추천해줘", "저알레르기 사료를 고려해 보세요.")]
    raw = {
        "relation": "NEW",
        "referenced_index": 1,
        "standalone_query": None,
        "resolution_confidence": 0.9,
    }
    assert validate_resolved_turn(raw, query="산책 코스 추천해줘", candidates=turns, pending=None) is None


def test_new_relation_does_not_leak_pending_axes() -> None:
    """Finding 1 / R8 회귀 — 지목 없는 NEW 는 대기 중인 되묻기의 축을 전혀 들고 있으면
    안 된다. 예전 코드는 `pending_missing_axes` 만 `anchored_to_pending` 에 걸려 있어서
    이 경우 `referenced_turn_id`·`pending_clarification_id` 는 비었는데 축만 새 나갔다.
    """
    pending = PendingClarification(
        turn_id=uuid.uuid4(),
        question="식욕과 활력 중 어느 쪽이 달라 보이나요?",
        missing_axes=[ObservationAxis.APPETITE],
    )
    raw = {
        "relation": "NEW",
        "referenced_index": None,
        "standalone_query": None,
        "resolution_confidence": 0.9,
    }
    resolved = validate_resolved_turn(raw, query="산책 코스 추천해줘", candidates=(), pending=pending)
    assert resolved is not None
    assert resolved.referenced_turn_id is None
    assert resolved.pending_clarification_id is None
    assert resolved.pending_missing_axes == []


def test_current_query_is_last_with_both_candidates_and_pending() -> None:
    """R11 — 후보 블록과 대기 되묻기 블록이 둘 다 있어도 CURRENT_QUERY 는 여전히 맨 뒤다."""
    turns = [_turn("사료 추천해줘", "저알레르기 사료를 고려해 보세요.")]
    pending = PendingClarification(
        turn_id=uuid.uuid4(),
        question="식욕과 활력 중 어느 쪽이 달라 보이나요?",
        missing_axes=[ObservationAxis.APPETITE],
    )
    prompt = build_turn_resolver_prompt(
        query="밥은 먹는데 계속 누워 있어", candidates=turns, pending=pending
    )
    candidate_index = prompt.index("CANDIDATE_TURNS:")
    pending_index = prompt.index("PENDING_CLARIFICATION:")
    query_index = prompt.index("CURRENT_QUERY:")
    assert candidate_index < query_index
    assert pending_index < query_index
    assert prompt.rstrip().endswith("CURRENT_QUERY: 밥은 먹는데 계속 누워 있어")
