import pytest
from pydantic import ValidationError

from daengs_evals.conversation_quality.cases import ConversationCase, Turn


def _case(**over):
    base = {
        "case_id": "cq_wellness_vague_01",
        "turns": [
            Turn(role="user", text="오늘 건강 상태는 어때?"),
            Turn(role="assistant", text="증상의 원인이나 병명은 여기서 판단하지 않아요."),
        ],
        "target_turns": [1],
        "state_snapshot": {"dog": {"breed": "요크셔테리어", "age_months": 60}},
        "user_input_needed": True,
        "expected_mode": "ASK",
        "repair_applicable": False,
        "source": "observed_2026_09",
        "version": 1,
    }
    return ConversationCase(**{**base, **over})


def test_turns_must_alternate_starting_with_user():
    with pytest.raises(ValidationError):
        _case(turns=[Turn(role="assistant", text="안녕하세요")])


def test_target_turn_must_point_at_an_assistant_turn():
    with pytest.raises(ValidationError):
        _case(target_turns=[0])


def test_target_turn_index_must_be_in_range():
    with pytest.raises(ValidationError):
        _case(target_turns=[9])


def test_expected_mode_is_an_interaction_mode_not_a_contract_status():
    # ANSWERED · CLARIFY 는 API 계약의 이름이다. 계약 결정이 뒤에 나도 케이스가
    # 안 흔들리도록 케이스는 상호작용 모드로만 적는다.
    with pytest.raises(ValidationError):
        _case(expected_mode="CLARIFY")


def test_case_carries_no_personal_identifier_fields():
    assert "user_id" not in ConversationCase.model_fields
    assert "app_user_id" not in ConversationCase.model_fields
