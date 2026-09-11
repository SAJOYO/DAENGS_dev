import uuid

import pytest

from daengs_backend.orchestration.contracts import ObservationAxis
from daengs_backend.orchestration.resolver import (
    MAX_ASSISTANT_CHARS,
    TURN_RESOLVER_PROMPT_VERSION,
    GeminiTurnResolver,
    PendingClarification,
    PriorTurn,
    ResolvedTurn,
    TurnRelation,
    TurnResolutionError,
    build_candidate_block,
    build_turn_resolver_prompt,
    conversation_context_of,
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


def test_conversation_context_of_fills_pending_question_when_anchored() -> None:
    """R14 — 되묻기에 실제로 답한 turn 은 문장 원문과 축을 둘 다 받는다."""
    pending = PendingClarification(
        turn_id=uuid.uuid4(),
        question="식욕과 활력 중 어느 쪽이 달라 보이나요?",
        missing_axes=[ObservationAxis.APPETITE, ObservationAxis.ENERGY],
    )
    resolved = ResolvedTurn(
        relation=TurnRelation.FOLLOW_UP,
        current_query="밥은 먹는데 계속 누워 있어",
        pending_clarification_id=pending.turn_id,
        pending_missing_axes=[ObservationAxis.APPETITE, ObservationAxis.ENERGY],
        resolution_confidence=0.9,
    )
    context = conversation_context_of(resolved, pending)
    assert context is not None
    assert context.pending_question == pending.question
    assert context.pending_missing_axes == [ObservationAxis.APPETITE, ObservationAxis.ENERGY]


def test_conversation_context_of_ignores_an_unanchored_pending() -> None:
    """R14 — 같이 넘어온 `pending` 이 이 turn 이 잇는 대상이 아니면 그대로 못 쓴다.

    `pending` 을 받았다는 사실만으로 채우면 Task 3 이 막았던 누수 모양(관계 없는 되묻기가
    새 turn 에 묻는 것)을 다시 연다.
    """
    other_pending = PendingClarification(
        turn_id=uuid.uuid4(), question="어느 발이 이상한가요?", missing_axes=[ObservationAxis.MOBILITY]
    )
    resolved = ResolvedTurn(
        relation=TurnRelation.FOLLOW_UP,
        current_query="사료는 얼마나 자주 바꿔야 해?",
        referenced_turn_id=uuid.uuid4(),
        resolution_confidence=0.9,
    )
    context = conversation_context_of(resolved, other_pending)
    assert context is not None
    assert context.pending_question is None
    assert context.pending_missing_axes == []


def test_conversation_context_of_with_no_pending_leaves_the_question_blank() -> None:
    resolved = ResolvedTurn(
        relation=TurnRelation.FOLLOW_UP,
        current_query="그거 얼마나 자주 해?",
        referenced_turn_id=uuid.uuid4(),
        resolution_confidence=0.9,
    )
    context = conversation_context_of(resolved, None)
    assert context is not None
    assert context.pending_question is None


def test_conversation_context_of_with_no_resolution_is_none() -> None:
    assert conversation_context_of(None, None) is None


def test_conversation_context_of_carries_the_referenced_assistant_answer_verbatim() -> None:
    """`conversation_context_of` 는 `ResolvedTurn.referenced_assistant_answer` 를 그대로
    옮기기만 한다 — 이미 `validate_resolved_turn` 이 잘랐으므로 여기서 다시 자르지 않는다.
    이 필드를 옮기는 줄을 지우면(`referenced_assistant_answer=` 인자를 빠뜨리면) 이 테스트가
    실패한다."""
    resolved = ResolvedTurn(
        relation=TurnRelation.REPEAT,
        current_query="아까 말한 거 다시 설명해줘",
        referenced_turn_id=uuid.uuid4(),
        referenced_original_request="아까 말한 거 다시 설명해줘",
        referenced_assistant_answer="소형견 저알레르기 사료를 하루 두 번 급여하세요.",
        resolution_confidence=0.85,
    )
    context = conversation_context_of(resolved, None)
    assert context is not None
    assert (
        context.referenced_assistant_answer
        == "소형견 저알레르기 사료를 하루 두 번 급여하세요."
    )


def test_conversation_context_of_pending_anchor_has_no_referenced_answer() -> None:
    """수용 케이스 — `relation=NEW` 이거나 대기 되묻기 앵커로 잡힌 turn 은 참조 턴이 없으므로
    `referenced_assistant_answer` 도 비어 있어야 한다(기존 유출 방지 게이트와 동일). 이
    필드에 아무 조건 없이 값을 채우면(예: pending.question 을 여기에 잘못 옮기면) 실패한다."""
    pending = PendingClarification(
        turn_id=uuid.uuid4(),
        question="식욕과 활력 중 어느 쪽이 달라 보이나요?",
        missing_axes=[ObservationAxis.APPETITE],
    )
    resolved = ResolvedTurn(
        relation=TurnRelation.FOLLOW_UP,
        current_query="밥은 먹는데 계속 누워 있어",
        pending_clarification_id=pending.turn_id,
        pending_missing_axes=[ObservationAxis.APPETITE],
        resolution_confidence=0.9,
    )
    context = conversation_context_of(resolved, pending)
    assert context is not None
    assert context.referenced_assistant_answer is None


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


def test_referenced_assistant_answer_is_truncated_not_the_raw_db_text() -> None:
    """followup-answer-text — `"아까 말한 거 다시 설명해줘"` 가 이력에 이미 있는 어지러운
    케이스(브리프 실측)를 재현한다. `referenced_original_request` 가 "아까 말한 거 다시
    설명해줘" 로 순환해도, 비서가 실제로 답한 내용이 실리면 General 이 그것을 다시 풀어
    쓸 수 있다. `PriorTurn.assistant` 는 `fit_candidates` 를 거쳐도 안 잘리므로(DB 상한
    8,000자) 여기서 400자로 잘라야 한다 — `validate_resolved_turn` 이 `truncate_assistant`
    를 거치지 않고 `referenced.assistant` 를 그대로 옮기면 이 테스트가 실패한다.
    """
    long_answer = "사료는 하루 두 번, 소형견 저알레르기 사료를 추천합니다. " * 20
    assert len(long_answer) > MAX_ASSISTANT_CHARS
    turns = [
        _turn("아까 말한 거 다시 설명해줘", "죄송해요, 무엇을 다시 설명해 드릴까요?"),
        _turn("우리 강아지 사료 추천해줘. 소형견이고 알레르기가 있어", long_answer),
        _turn("아까 말한 거 다시 설명해줘", "죄송해요, 무엇을 다시 설명해 드릴까요?"),
    ]
    raw = {
        "relation": "REPEAT",
        "referenced_index": 2,
        "standalone_query": "이전 대화에서 언급했던 강아지 사료 추천 내용을 다시 설명해줘",
        "resolution_confidence": 0.85,
    }
    resolved = validate_resolved_turn(
        raw, query="아까 말한 거 다시 설명해줘", candidates=turns, pending=None
    )
    assert resolved is not None
    assert resolved.referenced_assistant_answer == truncate_assistant(long_answer)
    assert resolved.referenced_assistant_answer is not None
    assert len(resolved.referenced_assistant_answer) == MAX_ASSISTANT_CHARS + 1
    assert resolved.referenced_assistant_answer.endswith("…")
    # 자른 것이지 원문 전체가 아니다.
    assert resolved.referenced_assistant_answer != long_answer


def test_no_referenced_index_leaves_the_assistant_answer_empty() -> None:
    """참조가 없으면(REPEAT/FOLLOW_UP 이 아무 후보도 안 짚으면) 답도 비어 있어야 한다 —
    `validate_resolved_turn` 이 `referenced` 가 `None` 인데도 값을 채우면 이 테스트가
    실패한다."""
    resolved = validate_resolved_turn(
        {"relation": "NEW", "referenced_index": None, "resolution_confidence": 1.0},
        query="산책 코스 추천해줘",
        candidates=(),
        pending=None,
    )
    assert resolved is not None
    assert resolved.referenced_assistant_answer is None


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


def test_follow_up_answering_pending_clarification_populates_the_anchor() -> None:
    """R12 — `test_new_relation_does_not_leak_pending_axes` 의 양성 짝. 사용자가 되묻기에
    답하는 것이 Turn Resolver 가 존재하는 이유인 경로이므로, `anchored_to_pending` 이
    반대로 뒤집히거나 더 좁아져도 붉게 실패해야 한다(수용 케이스 5)."""
    pending = PendingClarification(
        turn_id=uuid.uuid4(),
        question="식욕과 활력 중 어느 쪽이 달라 보이나요?",
        missing=["observation"],
        missing_axes=[ObservationAxis.APPETITE, ObservationAxis.ENERGY],
    )
    raw = {
        "relation": "FOLLOW_UP",
        "referenced_index": None,
        "standalone_query": "밥은 먹는데 활력이 없어 계속 누워 있음",
        "resolution_confidence": 0.9,
    }
    resolved = validate_resolved_turn(
        raw, query="밥은 먹는데 계속 누워 있어", candidates=(), pending=pending
    )
    assert resolved is not None
    assert resolved.pending_clarification_id == pending.turn_id
    assert resolved.pending_missing_axes == [ObservationAxis.APPETITE, ObservationAxis.ENERGY]
    assert resolved.referenced_turn_id is None
    assert resolved.current_query == "밥은 먹는데 계속 누워 있어"


def test_follow_up_naming_the_pending_clarification_by_index_still_anchors_to_pending() -> None:
    """Fix wave item 1. CLARIFY turn 은 `completed` 로 저장되므로 `candidates_of` 가 그것을
    후보 블록에도 넣는다(스펙 ⑥) — 같은 턴이 U{n}/A{n} 과 PENDING_CLARIFICATION 양쪽에
    나타난다. 모델이 그 되묻기를 번호로 지목해도(`referenced_index` 를 채워도) 대기 중인
    되묻기에 대한 답이라는 사실은 바뀌지 않는다 — 그런데 예전 `anchored_to_pending` 은
    `referenced is None` 을 요구해서 이 경로에서 pending 필드를 전부 놓쳤다."""
    pending_turn_id = uuid.uuid4()
    pending = PendingClarification(
        turn_id=pending_turn_id,
        question="식욕과 활력 중 어느 쪽이 달라 보이나요?",
        missing_axes=[ObservationAxis.APPETITE, ObservationAxis.ENERGY],
    )
    # 대기 중인 되묻기 자신이 후보 블록에도 나타난다 (CLARIFY 는 completed 로 저장된다).
    candidates = [
        PriorTurn(
            turn_id=pending_turn_id,
            user="밥은 잘 먹어?",
            assistant=pending.question,
        )
    ]
    raw = {
        "relation": "FOLLOW_UP",
        "referenced_index": 1,  # 모델이 대기 중인 되묻기를 번호로 지목했다.
        "standalone_query": "밥은 먹는데 활력이 없어 계속 누워 있음",
        "resolution_confidence": 0.9,
    }
    resolved = validate_resolved_turn(
        raw, query="밥은 먹는데 계속 누워 있어", candidates=candidates, pending=pending
    )
    assert resolved is not None
    assert resolved.pending_clarification_id == pending_turn_id
    assert resolved.pending_missing_axes == [ObservationAxis.APPETITE, ObservationAxis.ENERGY]
    assert resolved.referenced_turn_id is None
    assert resolved.context_used == [pending_turn_id]
    # 되묻기 문장은 이미 `pending_question` 으로 간다 — 여기서도 실으면 같은 문장이
    # 두 번 나간다(브리프 "안 하는 것"). `referenced = None` 널아웃이 `ResolvedTurn(...)`
    # 조립 전에 있어야 이 값이 비어 있다.
    assert resolved.referenced_assistant_answer is None


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


async def test_resolver_skips_the_model_entirely_on_the_fast_path() -> None:
    """수용 케이스 1 — 모델 호출 0회."""
    calls: list[str] = []

    async def _generate(prompt: str) -> object:
        calls.append(prompt)
        raise AssertionError("fast path must not call the model")

    resolver = GeminiTurnResolver(generate=_generate)
    resolved = await resolver.resolve(query="사료 추천해줘", candidates=(), pending=None)
    assert resolved.relation is TurnRelation.NEW
    assert calls == []


async def test_resolver_returns_the_validated_decision() -> None:
    turns = [_turn("심장사상충 예방약 먹여야 해?", "네, 보통 한 달에 한 번 투여합니다.")]

    async def _generate(prompt: str) -> object:
        return {
            "relation": "FOLLOW_UP",
            "referenced_index": 1,
            "standalone_query": "심장사상충 예방약을 얼마나 자주 먹여?",
            "resolution_confidence": 0.92,
        }

    resolver = GeminiTurnResolver(generate=_generate)
    resolved = await resolver.resolve(query="그거 얼마나 자주 해?", candidates=turns, pending=None)
    assert resolved.relation is TurnRelation.FOLLOW_UP
    assert resolved.referenced_turn_id == turns[0].turn_id


async def test_provider_failure_raises_the_contracted_error() -> None:
    async def _generate(prompt: str) -> object:
        raise TimeoutError("provider down")

    resolver = GeminiTurnResolver(generate=_generate)
    with pytest.raises(TurnResolutionError):
        await resolver.resolve(query="그거 얼마나 자주 해?", candidates=[_turn("a", "b")], pending=None)


async def test_unparseable_output_raises_the_contracted_error() -> None:
    async def _generate(prompt: str) -> object:
        return "{nope"

    resolver = GeminiTurnResolver(generate=_generate)
    with pytest.raises(TurnResolutionError):
        await resolver.resolve(query="그거 얼마나 자주 해?", candidates=[_turn("a", "b")], pending=None)


async def test_resolver_resolves_indices_against_the_fitted_candidate_list() -> None:
    """§4 — 인덱스는 원본 리스트가 아니라 fit_candidates 를 거친 리스트에 대해 풀린다.

    MAX_CANDIDATE_PAIRS(3) 를 넘는 후보를 넣으면 오래된 쌍이 잘려 나가고, 프롬프트의
    U1 은 잘린 뒤 리스트의 첫 항목을 가리킨다. 검증도 같은 fitted 리스트를 써야
    referenced_turn_id 가 실제로 프롬프트가 보여준 턴을 가리킨다.
    """
    turns = [
        _turn("첫 질문", "첫 답"),
        _turn("둘째 질문", "둘째 답"),
        _turn("셋째 질문", "셋째 답"),
        _turn("넷째 질문", "넷째 답"),
    ]
    fitted = fit_candidates(turns)
    assert fitted == turns[1:]  # 가장 오래된 한 쌍이 잘려 나간다

    async def _generate(prompt: str) -> object:
        assert "첫 질문" not in prompt
        return {
            "relation": "FOLLOW_UP",
            "referenced_index": 1,
            "standalone_query": "둘째 질문 이어서",
            "resolution_confidence": 0.9,
        }

    resolver = GeminiTurnResolver(generate=_generate)
    resolved = await resolver.resolve(query="그거 다시 알려줘", candidates=turns, pending=None)
    assert resolved.referenced_turn_id == fitted[0].turn_id
    assert resolved.referenced_turn_id != turns[0].turn_id
