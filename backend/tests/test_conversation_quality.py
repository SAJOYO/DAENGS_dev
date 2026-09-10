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


# --- Task 3: 루브릭 · 적용가능성 · 사용성 게이트 ---


def test_safety_failure_makes_the_turn_unusable_regardless_of_other_axes():
    from daengs_evals.conversation_quality.rubric import AxisScores, derive_usability

    u = derive_usability(
        AxisScores(response_mode_fit=2, context_continuity=2, repair_success=2),
        safety_failed=True,
    )
    assert u.usable is False and u.reason == "safety"


def test_response_mode_fit_zero_makes_the_turn_unusable():
    from daengs_evals.conversation_quality.rubric import AxisScores, derive_usability

    u = derive_usability(AxisScores(response_mode_fit=0), safety_failed=False)
    assert u.usable is False and u.reason == "response_mode_fit"


def test_repair_zero_is_unusable_only_when_repair_was_applicable():
    from daengs_evals.conversation_quality.rubric import AxisScores, derive_usability

    assert (
        derive_usability(
            AxisScores(response_mode_fit=2, repair_success=0), safety_failed=False
        ).usable
        is False
    )
    # 복구가 해당 없으면 None 이고, None 은 실패가 아니다
    assert (
        derive_usability(
            AxisScores(response_mode_fit=2, repair_success=None), safety_failed=False
        ).usable
        is True
    )


def test_axes_are_never_summed_into_a_total():
    from daengs_evals.conversation_quality.rubric import AxisScores

    assert not hasattr(AxisScores(response_mode_fit=2), "total")
    assert "total" not in AxisScores.model_fields
    assert "overall" not in AxisScores.model_fields


def test_not_asking_is_not_penalised_when_no_input_was_needed():
    from daengs_evals.conversation_quality.rubric import applicability

    case = _case(user_input_needed=False, state_snapshot={})
    applic = applicability(case, 1)
    assert applic["response_mode_fit"] is True
    # 상태가 없으면 상태를 안 썼다고 감점하지 않는다 — 잴 수 없으면 False(해당 없음)다
    assert applic["context_continuity"] is False


def test_axis_with_nothing_to_measure_is_not_applicable_rather_than_zero():
    from daengs_evals.conversation_quality.rubric import applicability

    # repair_applicable=False 인 케이스에서는 repair_success 를 잴 수 없다 —
    # 0 점이 아니라 False(해당 없음)로 나와야 한다.
    case = _case()
    assert case.repair_applicable is False
    assert applicability(case, 1)["repair_success"] is False


def test_superficial_profile_mention_does_not_count_as_state_use():
    from daengs_evals.conversation_quality.rubric import StateAudit

    audit = StateAudit(
        relevant_state_available=True,
        relevant_state_used=True,
        state_used_correctly=False,
        unsupported_or_superficial_personalization=True,
    )
    # 상태 감사는 사실 기록이지 점수가 아니다 — 점수로 승격되는 칸이 없어야 한다
    assert "score" not in StateAudit.model_fields
    assert audit.unsupported_or_superficial_personalization is True


def test_repair_applicability_is_per_target_turn_not_per_case():
    # Task 2 의 관찰 케이스: target_turns=[1, 5, 7], repair_applicable=True.
    # 턴 1 은 앞에 복구할 assistant 턴이 없어 repair_success 의 대상이 될 수 없다 —
    # 케이스 단위로 답하면 턴 1 에서도 복구를 재려 하거나(오판) 턴 1 을 통째로
    # 빼야 한다(스펙의 헤드라인 실패를 놓친다). 그래서 적용가능성은 턴마다 갈린다.
    from daengs_evals.conversation_quality import CASES_V1_PATH
    from daengs_evals.conversation_quality.cases import load_cases
    from daengs_evals.conversation_quality.rubric import applicability

    observed = next(
        c for c in load_cases(CASES_V1_PATH) if c.case_id == "cq_observed_wellness_repair_01"
    )
    assert applicability(observed, 1)["repair_success"] is False
    assert applicability(observed, 5)["repair_success"] is True


# --- Task 4: 코드 기반 검사 ---


def test_prior_turns_do_not_reach_inference_today():
    from daengs_evals.conversation_quality.transcript import PRIOR_TURNS_REACH_INFERENCE

    # 이 상수가 True 로 바뀌는 순간 두 축의 정답이 0 이 아니게 된다.
    # 런타임이 바뀌면 여기부터 고친다.
    assert PRIOR_TURNS_REACH_INFERENCE is False


def test_repeat_count_counts_identical_assistant_messages():
    from daengs_evals.conversation_quality.transcript import check_transcript

    checks = check_transcript(
        assistant_texts=["증상의 원인이나 병명은", "다른 답", "증상의 원인이나 병명은"]
    )
    assert checks.max_repeat_count == 2


def test_refusal_source_is_ambiguous_for_the_off_topic_sentence():
    from daengs_backend.orchestration.redirects import NO_CAPABILITY_MESSAGE
    from daengs_evals.conversation_quality.transcript import check_transcript

    checks = check_transcript(assistant_texts=[NO_CAPABILITY_MESSAGE])
    # 문장만으로는 General reason=off_topic 인지 빈 계획 FAILED 인지 못 가린다
    assert checks.refusal_source_ambiguous is True


def test_scored_rows_exclude_empty_and_error_turns():
    from daengs_evals.conversation_quality.transcript import check_transcript

    checks = check_transcript(assistant_texts=["", "정상 답"])
    assert checks.excluded_before_judging == {"not_answered": 1}


# --- Task 5: 드라이버 이음매와 랩 수집 ---


def test_stateless_driver_sends_only_the_current_query():
    from daengs_evals.conversation_quality.drivers import FakeDriver

    # 오늘의 런타임을 그대로 흉내낸다 — 드라이버가 이전 턴을 안 싣는다는 것이 계약이다
    driver = FakeDriver(replies=["a", "b"])
    driver.send("첫 질문")
    driver.send("둘째 질문")
    assert driver.seen_payloads == [{"query": "첫 질문"}, {"query": "둘째 질문"}]


def test_lap_header_pins_the_six_things_that_must_not_move():
    from daengs_evals.conversation_quality.collect import LapHeader

    header = LapHeader(
        lap="before",
        cases_sha256="a" * 64,
        judge_model="gpt-5.4-2026-03-05",
        prompt_version=1,
        anchor_set="dev",
        adapter_mode="fake",
    )
    for field in ("cases_sha256", "judge_model", "prompt_version", "anchor_set", "adapter_mode"):
        assert field in header.model_dump()


def test_collect_records_the_response_time_snapshot_not_a_later_db_read(tmp_path):
    import json

    from daengs_evals.conversation_quality.collect import run_collect
    from daengs_evals.conversation_quality.drivers import FakeDriver

    case = _case()
    out = run_collect(cases=[case], driver=FakeDriver(replies=["답"]), out_dir=tmp_path, lap="t")
    row = json.loads(out.read_text("utf-8").splitlines()[1])
    assert row["state_supplied"] == case.state_snapshot  # 재조회가 아니라 그 시점 값


def test_collect_makes_no_live_call_in_tests():
    # 자동 테스트는 가짜만 쓴다. 실제 provider 모듈을 import 하지 않는다.
    import daengs_evals.conversation_quality.collect as m

    assert "openai" not in m.__dict__


# --- Task 5 fixes: 코디네이터 리뷰 반영 ---


def test_answered_by_fake_adapter_real_mode_is_never_fake():
    from daengs_evals.conversation_quality.collect import _answered_by_fake_adapter

    assert _answered_by_fake_adapter("real", "general") is False


def test_answered_by_fake_adapter_fake_mode_is_always_fake():
    from daengs_evals.conversation_quality.collect import _answered_by_fake_adapter

    assert _answered_by_fake_adapter("fake", "training") is True


def test_answered_by_fake_adapter_fallback_only_marks_general_as_real():
    from daengs_evals.conversation_quality.collect import _answered_by_fake_adapter

    assert _answered_by_fake_adapter("fallback-only", "general") is False
    assert _answered_by_fake_adapter("fallback-only", "training") is True


def test_answered_by_fake_adapter_is_not_applicable_when_no_capability_ran():
    from daengs_evals.conversation_quality.collect import _answered_by_fake_adapter

    # 계획 자체가 없거나(스몰토크) FAILED 로 끝나 어떤 capability 도 안 뛴 턴 —
    # "가짜가 답했다" 는 질문 자체가 성립하지 않는다.
    assert _answered_by_fake_adapter("real", None) is None


def test_route_plan_dump_drops_the_dog_profile_payload():
    import json

    from daengs_backend.orchestration.contracts import (
        CapabilityName,
        CapabilityRequest,
        DogContext,
        GeneralPayload,
        RoutePlan,
        RouterKind,
    )
    from daengs_evals.conversation_quality.drivers import _sanitize_route_plan

    plan = RoutePlan(
        requests=[
            CapabilityRequest(
                capability=CapabilityName.GENERAL,
                payload=GeneralPayload(
                    question="밥은 얼마나 줘야 해?",
                    dog=DogContext(breed="dog_pug", age_months=24, on_medication=True),
                ),
            )
        ],
        router=RouterKind.LLM,
        model="gemini-test",
        prompt_version="v3",
    )
    dumped = _sanitize_route_plan(plan)
    dumped_text = json.dumps(dumped, ensure_ascii=False)
    assert "payload" not in dumped
    assert "dog_pug" not in dumped_text
    assert "on_medication" not in dumped_text
    assert dumped["capabilities"] == ["general"]


# --- Task 6: 세 축 판정기 ---

#: 실제 핀(`settings.openai_judge_model`)을 테스트에 박지 않는다 — 핀이 바뀌면 이 파일이
#: 함께 빨개질 이유가 없고, 무엇보다 **테스트에서 진짜 모델 이름을 쓰면** 나중에 실제
#: 앵커 기록 파일과 이름이 겹칠 수 있다.
FAKE_JUDGE_MODEL = "fake-judge-0000-00-00"


#: 앵커 통과 기록이 말하는 앵커 해시. 테스트에서는 값이 무엇인지가 아니라 **대조가 되는지**가
#: 요점이라 아무 64자나 쓴다.
FAKE_ANCHORS_SHA256 = "b" * 64


def _fake_verdict(score=0, axis="response_mode_fit"):
    """축마다 스키마가 다르므로 가짜도 갈린다 — 하나로 뭉개면 스키마 분기가 안 잡힌다."""
    from daengs_evals.conversation_quality.judge import ContinuityVerdict, Verdict

    if axis == "context_continuity":
        return ContinuityVerdict(
            observations=["앞 턴을 다시 묻는다"],
            rationale="근거",
            relevant_state_used=False,
            state_used_correctly=False,
            unsupported_or_superficial_personalization=True,
            score=score,
        )
    return Verdict(observations=["앞 턴을 다시 묻는다"], rationale="근거", score=score)


def _repair_case():
    """정정 뒤 같은 답이 다시 나오는 케이스 — 세 축이 모두 적용되는 최소 모양."""
    return _case(
        case_id="cq_repair_probe_01",
        turns=[
            Turn(role="user", text="우리 동네 산책 코스 알려줘"),
            Turn(role="assistant", text="산책은 하루 두 번이 좋습니다."),
            Turn(role="user", text="그게 아니라 코스를 알려달라고요"),
            Turn(role="assistant", text="산책은 하루 두 번이 좋습니다."),
        ],
        target_turns=[3],
        repair_applicable=True,
    )


def _lap_rows(case, reply="산책은 하루 두 번이 좋습니다."):
    """Task 5 의 산출물을 그대로 판정기에 먹인다 — 손으로 만든 dict 를 쓰면 랩 파일의
    실제 모양이 바뀌어도 이 테스트가 안 깨진다."""
    from daengs_evals.conversation_quality.collect import target_turn_row
    from daengs_evals.conversation_quality.drivers import FakeDriver

    driver = FakeDriver(replies=[reply] * len(case.target_turns))
    return [target_turn_row(case, i, driver).model_dump() for i in case.target_turns]


def _anchor_pass(tmp_path, *, anchor_set="dev", model=FAKE_JUDGE_MODEL, **over):
    import json

    from daengs_evals.conversation_quality.judge import anchor_record_name

    name = anchor_record_name(anchor_set=anchor_set, model=model)
    record = {"passed": True, "anchors_sha256": FAKE_ANCHORS_SHA256, **over}
    (tmp_path / name).write_text(json.dumps(record), encoding="utf-8")
    return tmp_path


def test_each_axis_is_a_separate_call_with_its_own_inputs(tmp_path):
    from daengs_evals.conversation_quality.judge import run_score

    calls = []
    case = _repair_case()
    run_score(
        rows=_lap_rows(case),
        cases=[case],
        model=FAKE_JUDGE_MODEL,
        anchor_dir=_anchor_pass(tmp_path),
        anchors_sha256=FAKE_ANCHORS_SHA256,
        generate=lambda **kw: calls.append(kw) or _fake_verdict(axis=kw["axis"]),
    )
    assert [c["axis"] for c in calls] == [
        "response_mode_fit",
        "context_continuity",
        "repair_success",
    ]
    # 근거 오염 방지: 모드 판정기는 앞 턴도 프로필도 안 본다
    assert "prior_turns" not in calls[0]["payload"]
    assert "state_supplied" not in calls[0]["payload"]
    # 복구 판정기에 상태를 주면 «그럴듯한 개인화»로 정정 실패를 합리화한다
    assert "state_supplied" not in calls[2]["payload"]


def test_rationale_is_generated_before_the_score():
    from daengs_evals.conversation_quality.judge import Verdict

    # 칸 순서가 곧 생성 순서다 — 점수를 위에 두면 모델이 답을 정해 놓고 이유를 붙인다
    assert list(Verdict.model_fields) == ["observations", "rationale", "score"]


def test_na_axes_are_not_called_at_all(tmp_path):
    from daengs_evals.conversation_quality.judge import run_score

    calls = []
    case = _case()  # repair_applicable=False
    judgments = run_score(
        rows=_lap_rows(case),
        cases=[case],
        model=FAKE_JUDGE_MODEL,
        anchor_dir=_anchor_pass(tmp_path),
        anchors_sha256=FAKE_ANCHORS_SHA256,
        generate=lambda **kw: calls.append(kw) or _fake_verdict(axis=kw["axis"]),
    )
    assert "repair_success" not in [c["axis"] for c in calls]
    # 못 잰 축은 0 이 아니라 없음이다
    assert judgments[0].scores.repair_success is None
    assert "repair_success" in judgments[0].not_applicable


def test_score_refuses_to_run_without_an_anchor_pass_record(tmp_path):
    from daengs_evals.conversation_quality.judge import run_score

    case = _repair_case()
    with pytest.raises(SystemExit):
        run_score(  # 통과 기록 없음
            rows=_lap_rows(case),
            cases=[case],
            model=FAKE_JUDGE_MODEL,
            anchor_dir=tmp_path,
            anchors_sha256=FAKE_ANCHORS_SHA256,
            generate=lambda **kw: _fake_verdict(axis=kw["axis"]),
        )


def test_judgment_file_header_pins_the_judge_model_and_prompt_version(tmp_path):
    import json

    from daengs_evals.conversation_quality.judge import PROMPT_VERSION, run_score

    case = _repair_case()
    out = tmp_path / "judgments_t.jsonl"
    run_score(
        rows=_lap_rows(case),
        cases=[case],
        model=FAKE_JUDGE_MODEL,
        anchor_dir=_anchor_pass(tmp_path),
        anchors_sha256=FAKE_ANCHORS_SHA256,
        generate=lambda **kw: _fake_verdict(axis=kw["axis"]),
        lap="t",
        out_path=out,
    )
    header = json.loads(out.read_text("utf-8").splitlines()[0])
    assert header["judge_model"] == FAKE_JUDGE_MODEL
    assert header["prompt_version"] == PROMPT_VERSION
    # 이름만으로는 어떤 앵커를 통과했는지 모른다 — 판정 파일이 그것을 들고 있어야 한다
    assert header["anchors_sha256"] == FAKE_ANCHORS_SHA256


def test_the_expected_mode_label_never_reaches_the_judge(tmp_path):
    import json
    from typing import get_args

    from daengs_evals.conversation_quality.cases import ExpectedMode
    from daengs_evals.conversation_quality.judge import PROMPTS, run_score

    calls = []
    case = _repair_case()
    run_score(
        rows=_lap_rows(case),
        cases=[case],
        model=FAKE_JUDGE_MODEL,
        anchor_dir=_anchor_pass(tmp_path),
        anchors_sha256=FAKE_ANCHORS_SHA256,
        generate=lambda **kw: calls.append(kw) or _fake_verdict(axis=kw["axis"]),
    )
    sent = json.dumps([c["payload"] for c in calls], ensure_ascii=False)
    sent += "\n".join(c["prompt"] for c in calls) + "\n".join(PROMPTS.values())
    # 정답지를 보여주면 판정기가 정확도 채점기로 변한다 — 모드 이름 **전부**가 새면 안 된다.
    # 케이스 하나의 모드만 보면 나머지 모드 이름이 프롬프트에 박혀도 안 잡힌다.
    for label in ("expected_mode", "repair_applicable", *get_args(ExpectedMode)):
        assert label not in sent


def test_state_audit_comes_from_the_judge_not_from_a_score(tmp_path):
    import json

    from daengs_evals.conversation_quality.judge import ContinuityVerdict, run_score

    case = _case()  # 상태가 있고 target_turns=[1] — 앞 턴은 없다
    out = tmp_path / "judgments_t.jsonl"
    judgments = run_score(
        rows=_lap_rows(case),
        cases=[case],
        model=FAKE_JUDGE_MODEL,
        anchor_dir=_anchor_pass(tmp_path),
        anchors_sha256=FAKE_ANCHORS_SHA256,
        generate=lambda **kw: _fake_verdict(axis=kw["axis"]),
        out_path=out,
    )
    audit = judgments[0].state_audit
    # 네 칸 중 하나는 payload 가 알고(상태가 실렸나), 셋은 판정기가 답한다
    assert audit.relevant_state_available is True
    assert audit.relevant_state_used is False
    assert audit.unsupported_or_superficial_personalization is True
    # 서수 하나로 뭉개지 않고 판정 파일에 그대로 남아야 Task 8 이 사실 집계를 낼 수 있다
    row = json.loads(out.read_text("utf-8").splitlines()[1])
    assert row["state_audit"]["unsupported_or_superficial_personalization"] is True
    assert row["verdicts"]["context_continuity"]["relevant_state_used"] is False
    # 세 칸이 점수보다 **먼저** 생성돼야 한다 — 뒤에 두면 점수를 정해 놓고 칸을 맞춘다
    fields = list(ContinuityVerdict.model_fields)
    assert fields.index("unsupported_or_superficial_personalization") < fields.index("score")


def test_state_audit_is_absent_when_the_axis_was_not_measured(tmp_path):
    from daengs_evals.conversation_quality.judge import run_score

    # 상태도 없고 앞 턴도 없다 → context_continuity 자체가 해당 없음
    case = _case(user_input_needed=False, state_snapshot={})
    judgments = run_score(
        rows=_lap_rows(case),
        cases=[case],
        model=FAKE_JUDGE_MODEL,
        anchor_dir=_anchor_pass(tmp_path),
        anchors_sha256=FAKE_ANCHORS_SHA256,
        generate=lambda **kw: _fake_verdict(axis=kw["axis"]),
    )
    # 감사를 안 한 것과 "안 썼다" 는 다르다
    assert judgments[0].state_audit is None


def test_anchor_record_must_say_which_anchors_it_passed(tmp_path):
    import json

    from daengs_evals.conversation_quality.judge import anchor_record_name, require_anchor_pass

    name = anchor_record_name(anchor_set="dev", model=FAKE_JUDGE_MODEL)
    (tmp_path / name).write_text(json.dumps({"passed": True}), encoding="utf-8")
    # 옛 모양의 기록 — 무엇을 통과한 것인지 말하지 못하면 게이트가 아니다
    with pytest.raises(SystemExit):
        require_anchor_pass(
            tmp_path,
            anchor_set="dev",
            model=FAKE_JUDGE_MODEL,
            anchors_sha256=FAKE_ANCHORS_SHA256,
        )


def test_gate_catches_anchors_edited_after_the_pass_record(tmp_path):
    from daengs_evals.conversation_quality.judge import require_anchor_pass

    _anchor_pass(tmp_path)
    # 앵커를 고치거나 늘려도 파일 이름은 그대로다 — 해시 대조 말고는 못 잡는 자리
    with pytest.raises(SystemExit):
        require_anchor_pass(
            tmp_path, anchor_set="dev", model=FAKE_JUDGE_MODEL, anchors_sha256="c" * 64
        )
    # 같은 해시면 통과한다
    record = require_anchor_pass(
        tmp_path, anchor_set="dev", model=FAKE_JUDGE_MODEL, anchors_sha256=FAKE_ANCHORS_SHA256
    )
    assert record["anchors_sha256"] == FAKE_ANCHORS_SHA256


def test_user_input_needed_is_collinear_with_the_answer_key_today():
    from collections import Counter

    from daengs_evals.conversation_quality import CASES_V1_PATH
    from daengs_evals.conversation_quality.cases import load_cases

    cases = load_cases(CASES_V1_PATH)
    dist = Counter((c.user_input_needed, c.expected_mode) for c in cases)
    # judge.py 가 이 수를 두 자리(모듈 머리말 · build_payload)에 적어 두고 있다. 그 수가
    # 문서의 산출물이라 여기서 못 박는다 — **이 테스트가 깨지면 세트가 바뀐 것이고, 그러면
    # judge.py 의 두 주석을 같이 고쳐야 한다.** 겹침이 깨지는 쪽이 목표다 (Task 7 의 앵커).
    assert dist == {(True, "ASK"): 5, (False, "ANSWER"): 6, (False, "REDIRECT"): 2}
    need = [c for c in cases if c.user_input_needed]
    assert len(need) == 5 and all(c.expected_mode == "ASK" for c in need)


def test_the_anchor_hash_check_cannot_be_skipped_by_omitting_it(tmp_path):
    from daengs_evals.conversation_quality.judge import require_anchor_pass, run_score

    case = _case()
    # 넘길 수 있게만 해 두면 **안 넘기는 길이 기본 경로**가 되고, 그러면 기록은 있고
    # passed 는 참인데 해시는 아무도 안 보는 상태가 그대로 남는다. 서명으로 든다.
    with pytest.raises(TypeError):
        run_score(
            rows=_lap_rows(case),
            cases=[case],
            model=FAKE_JUDGE_MODEL,
            anchor_dir=_anchor_pass(tmp_path),
            generate=lambda **kw: _fake_verdict(axis=kw["axis"]),
        )
    with pytest.raises(TypeError):
        require_anchor_pass(tmp_path, anchor_set="dev", model=FAKE_JUDGE_MODEL)


def test_a_failed_anchor_run_does_not_open_the_gate(tmp_path):
    from daengs_evals.conversation_quality.judge import require_anchor_pass

    _anchor_pass(tmp_path, passed=False)  # 돌려는 봤고, 통과는 못 했다
    with pytest.raises(SystemExit):
        require_anchor_pass(
            tmp_path,
            anchor_set="dev",
            model=FAKE_JUDGE_MODEL,
            anchors_sha256=FAKE_ANCHORS_SHA256,
        )


def test_a_malformed_anchor_record_is_a_readable_stop_not_a_traceback(tmp_path):
    from daengs_evals.conversation_quality.judge import anchor_record_name, require_anchor_pass

    name = anchor_record_name(anchor_set="dev", model=FAKE_JUDGE_MODEL)
    (tmp_path / name).write_text("{깨진 json", encoding="utf-8")
    with pytest.raises(SystemExit):
        require_anchor_pass(
            tmp_path,
            anchor_set="dev",
            model=FAKE_JUDGE_MODEL,
            anchors_sha256=FAKE_ANCHORS_SHA256,
        )


def test_rows_the_seam_never_answered_are_not_judged(tmp_path):
    from daengs_evals.conversation_quality.drivers import NOT_REACHED
    from daengs_evals.conversation_quality.judge import run_score

    calls = []
    case = _repair_case()
    rows = _lap_rows(case)
    rows[0]["message"] = NOT_REACHED  # 답이 아니라 "이 이음매로는 못 봤다" 는 표시다
    judgments = run_score(
        rows=rows,
        cases=[case],
        model=FAKE_JUDGE_MODEL,
        anchor_dir=_anchor_pass(tmp_path),
        anchors_sha256=FAKE_ANCHORS_SHA256,
        generate=lambda **kw: calls.append(kw) or _fake_verdict(axis=kw["axis"]),
    )
    # 센티널을 채점시키면 판정기가 우리 문자열에 점수를 매긴다
    assert calls == []
    assert judgments == []


def test_the_judgment_file_says_how_many_rows_it_skipped(tmp_path):
    import json

    from daengs_evals.conversation_quality.drivers import NOT_REACHED
    from daengs_evals.conversation_quality.judge import run_score

    case = _case()
    rows = _lap_rows(case) + _lap_rows(case) + _lap_rows(case)
    rows[0]["message"] = NOT_REACHED
    rows[1]["message"] = "   "
    out = tmp_path / "judgments_t.jsonl"
    run_score(
        rows=rows,
        cases=[case],
        model=FAKE_JUDGE_MODEL,
        anchor_dir=_anchor_pass(tmp_path),
        anchors_sha256=FAKE_ANCHORS_SHA256,
        generate=lambda **kw: _fake_verdict(axis=kw["axis"]),
        out_path=out,
    )
    header = json.loads(out.read_text("utf-8").splitlines()[0])
    # 판정 파일이 자기를 설명해야 한다 — 리포트가 랩 파일과 차집합을 뜨게 두지 않는다
    assert (header["items"], header["skipped"]) == (1, 2)


def test_judge_makes_no_live_call_at_import_time():
    import daengs_evals.conversation_quality.judge as m

    # provider 클라이언트도 API 키도 import 만으로는 필요 없어야 한다
    assert "openai" not in m.__dict__
    assert "settings" not in m.__dict__
