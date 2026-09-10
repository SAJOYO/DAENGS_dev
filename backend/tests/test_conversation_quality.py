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


PINNED_277_SHA256 = "4e6f6462d152701186786b968a3cd920ddd125d07b95f9fd46bbfe4a7b440fa6"


def test_cases_v1_loads_and_covers_the_required_shapes():
    from daengs_evals.conversation_quality import CASES_V1_PATH
    from daengs_evals.conversation_quality.cases import load_cases

    cases = load_cases(CASES_V1_PATH)
    assert 12 <= len(cases) <= 20
    assert len({c.case_id for c in cases}) == len(cases)
    modes = {c.expected_mode for c in cases}
    assert modes == {"ANSWER", "ASK", "REDIRECT"}
    assert any(c.repair_applicable for c in cases)
    assert any(not c.user_input_needed for c in cases)
    assert any(c.state_snapshot for c in cases)
    assert any(not c.state_snapshot for c in cases)


def test_observed_scenario_keeps_its_correction_and_repetition_structure():
    from daengs_evals.conversation_quality import CASES_V1_PATH
    from daengs_evals.conversation_quality.cases import load_cases

    observed = next(
        c for c in load_cases(CASES_V1_PATH) if c.case_id == "cq_observed_wellness_repair_01"
    )
    user_turns = [t.text for t in observed.turns if t.role == "user"]
    # 같은 요청이 되풀이됐다는 것이 이 케이스의 본질이다 — 한 턴으로 줄이면 사라진다.
    assert sum("건강 상태" in t for t in user_turns) >= 3
    assert observed.repair_applicable is True
    assert observed.expected_mode == "ASK"


def test_the_frozen_277_question_set_is_untouched():
    from daengs_evals.answer_quality.questions import QUESTIONS_V1_PATH
    from daengs_evals.conversation_quality.cases import file_sha256

    # answer_quality.questions.file_sha256 (바이트 해시) 를 여기서 쓰면 안 된다 — 이
    # 저장소는 questions_v1.jsonl 을 .gitattributes 로 안 고정해서 git 블롭은 LF, 이
    # Windows 워킹 카피는 core.autocrlf=true 때문에 CRLF 다. 바이트 해시로 값을 박으면
    # 그 값을 만든 체크아웃과 CI(ubuntu-latest, LF 로 체크아웃)가 서로 다른 값을 내서
    # 이 가드가 세트 변경과 무관하게 상시 빨간불이 된다. 그래서 여기서는 LF 정규화 텍스트를
    # 해시하는 conversation_quality.cases.file_sha256 을 대신 쓴다 — 경로만
    # answer_quality 것을 빌려 온다.
    assert file_sha256(QUESTIONS_V1_PATH) == PINNED_277_SHA256


def test_repair_applicable_needs_only_one_target_turn_at_or_after_index_two():
    # repair_applicable 은 "어느 대상 턴에선가 복구가 성립한다"는 뜻이지 모든 대상 턴이
    # 복구 대상이라는 뜻이 아니다 — 턴별 적용가능성은 판정 시점(Task 3)의 일이다.
    with pytest.raises(ValidationError):
        _case(target_turns=[1], repair_applicable=True)

    case = _case(
        turns=[
            Turn(role="user", text="오늘 건강 상태는 어때?"),
            Turn(role="assistant", text="증상의 원인이나 병명은 여기서 판단하지 않아요."),
            Turn(role="user", text="오늘 힘이 없어 보이는데?"),
            Turn(role="assistant", text="식욕이나 배변 상태 등 다른 변화가 있는지 관찰해 주세요."),
        ],
        target_turns=[1, 3],
        repair_applicable=True,
    )
    assert case.target_turns == [1, 3]
