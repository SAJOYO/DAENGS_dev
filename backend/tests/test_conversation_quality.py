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
    # Windows 워킹 카피는 core.autocrlf=true 때문에 CRLF 다. GitHub 호스티드 CI 는 이
    # 저장소에서 과금 문제로 애초에 안 돈다(잡히기 전에 취소된다) — 근거는 그게 아니라
    # **다른 개발자의 체크아웃**이다: 팀원이 Linux·WSL 에서 체크아웃하면 그쪽은 LF 라,
    # 바이트 해시로 값을 박으면 그 값을 만든 체크아웃과 그쪽이 서로 다른 값을 내서 이
    # 가드가 세트 변경과 무관하게, 체크아웃 방식에 따라서만 빨간불이 된다. 그래서 여기서는
    # LF 정규화 텍스트를 해시하는 conversation_quality.cases.file_sha256 을 대신 쓴다 —
    # 경로만 answer_quality 것을 빌려 온다.
    assert file_sha256(QUESTIONS_V1_PATH) == PINNED_277_SHA256


def test_repair_applicable_needs_only_one_target_turn_at_or_after_index_two():
    # repair_applicable 은 "어느 대상 턴에선가 복구가 성립한다"는 뜻이지 모든 대상 턴이
    # 복구 대상이라는 뜻이 아니다 — 턴별 적용가능성은 판정 시점(`rubric.applicability`)의 일이다.
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


# --- 루브릭 · 적용가능성 · 사용성 게이트 (rubric.py) ---


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


def test_context_continuity_is_applicable_at_turn_index_two_even_without_state():
    from daengs_evals.conversation_quality.rubric import applicability

    # `turn_index >= 2 or bool(case.state_snapshot)` 의 첫 갈래만으로도 적용 가능해야
    # 한다 — 상태가 아예 없어도(빈 `state_snapshot`) 앞 턴이 있으면 이을 것이 있다.
    case = _case(
        turns=[
            Turn(role="user", text="오늘 건강 상태는 어때?"),
            Turn(role="assistant", text="증상의 원인이나 병명은 여기서 판단하지 않아요."),
            Turn(role="user", text="그래서 뭘 봐야 하는데?"),
            Turn(role="assistant", text="식욕이나 배변 상태 등을 관찰해 주세요."),
        ],
        target_turns=[3],
        state_snapshot={},
    )
    assert applicability(case, 3)["context_continuity"] is True


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
    # 관찰 케이스: target_turns=[1, 5, 7], repair_applicable=True.
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


# --- 코드 기반 검사 (transcript.py) ---


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


# --- 드라이버 이음매와 랩 수집 (drivers.py · collect.py) ---


def test_fake_driver_records_only_the_current_query():
    from daengs_evals.conversation_quality.drivers import FakeDriver

    # `FakeDriver` 는 이전 턴을 안 싣는다 — `StatelessDriver.send` 와 같은 계약이다
    # (오늘의 런타임이 실제로 그렇다).
    driver = FakeDriver(replies=["a", "b"])
    driver.send("첫 질문")
    driver.send("둘째 질문")
    assert driver.seen_payloads == [{"query": "첫 질문"}, {"query": "둘째 질문"}]


def test_lap_header_pins_the_five_things_run_collect_actually_recorded(tmp_path):
    from daengs_evals.conversation_quality.collect import load_lap, run_collect
    from daengs_evals.conversation_quality.drivers import FakeDriver

    case = _case()
    out = run_collect(
        cases=[case],
        driver=FakeDriver(replies=["답"]),
        out_dir=tmp_path,
        lap="before",
        judge_model="gpt-5.4-2026-03-05",
        prompt_version=7,
        anchor_set="holdout",
    )
    lap_meta, _ = load_lap(out)
    # 값 자체가 실제로 실렸는지를 본다 — 필드가 모델에 있다는 것만으로는 아무것도 못 잡는다.
    assert lap_meta["cases_sha256"] != ""
    assert lap_meta["judge_model"] == "gpt-5.4-2026-03-05"
    assert lap_meta["prompt_version"] == 7
    assert lap_meta["anchor_set"] == "holdout"
    assert lap_meta["adapter_mode"] == "fake-driver"


def test_collect_records_the_response_time_snapshot_not_a_later_db_read(tmp_path):
    import json

    from daengs_evals.conversation_quality.collect import run_collect
    from daengs_evals.conversation_quality.drivers import FakeDriver

    case = _case()
    out = run_collect(cases=[case], driver=FakeDriver(replies=["답"]), out_dir=tmp_path, lap="t")
    row = json.loads(out.read_text("utf-8").splitlines()[1])
    assert row["state_supplied"] == case.state_snapshot  # 재조회가 아니라 그 시점 값


# `collect.py` 가 실제로 어느 provider 를 무는지(Gemini)는
# `test_every_module_in_the_package_imports_without_backend_settings` 가 이미 잡는다 —
# 그 테스트는 "이 패키지의 어떤 모듈도 import 만으로는 backend 설정을 안 문다"는 속성을
# 직접 재므로, 여기서 `m.__dict__` 에 없는 모듈 이름을 확인하는 것(그나마도 틀린 이름이었다
# — collect.py 는 OpenAI 가 아니라 Gemini 를 문다)은 더 약한 중복이다.


# --- 드라이버 이음매·랩 수집 리뷰 반영 ---


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


def test_fake_driver_adapter_mode_does_not_collide_with_the_real_fake_mode():
    from daengs_evals.conversation_quality.drivers import FakeDriver

    # `FakeDriver`(오케스트레이터 자체를 안 돌린다)와 `--adapter-mode fake`(진짜
    # 오케스트레이터 + 가짜 capability 어댑터)는 서로 다른 이음매다 — 헤더의 `adapter_mode`
    # 가 같은 문자열이면 `render_compare` 가 그 둘을 구별 못 한다.
    assert FakeDriver.adapter_mode == "fake-driver"
    assert FakeDriver.adapter_mode != "fake"


def test_answered_by_fake_adapter_recognises_fake_driver_regardless_of_capability():
    from daengs_evals.conversation_quality.collect import _answered_by_fake_adapter
    from daengs_evals.conversation_quality.drivers import NOT_REACHED

    # `FakeDriver` 는 capability 를 안 실어 보내(`NOT_REACHED`) 예전 로직으로는 여기서
    # `NOT_REACHED` 가 나왔다 — 그러면 100% 합성 랩이 리포트에서 "전부 측정됨"으로 보인다.
    assert _answered_by_fake_adapter("fake-driver", NOT_REACHED) is True
    assert _answered_by_fake_adapter("fake-driver", None) is True
    assert _answered_by_fake_adapter("fake-driver", "training") is True


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


# --- 세 축 판정기 (judge.py) ---

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
    """랩 수집(collect.target_turn_row)의 산출물을 그대로 판정기에 먹인다 — 손으로 만든 dict 를 쓰면 랩 파일의
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
    # 서수 하나로 뭉개지 않고 판정 파일에 그대로 남아야 report.summarize 가 사실 집계를 낼 수 있다
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
    # judge.py 의 두 주석을 같이 고쳐야 한다.** 겹침이 깨지는 쪽이 목표다 (anchors.py 의 앵커).
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


# --- 크래시 저항 — 증분 기록 · 이어 돌리기 · 콜 하나 실패해도 랩을 안 잃는다 (#401) ---


def test_a_partial_file_is_readable_after_a_mid_loop_crash(tmp_path):
    """한 랩이 39 콜인데 어디선가 죽으면 이미 낸 판정까지 잃는다 — 그게 이 카드의 문제다.

    `KeyboardInterrupt` 는 `Exception` 이 아니라 `run_score` 의 콜별 방어(요구사항 3)로도
    안 잡힌다 — 그래서 여기서는 «죽는다»를 그대로 흉내 낼 수 있다. 죽기 전까지 쓴 파일이
    `load_judgments` 로 읽히면, 헤더가 아직 최종값이 아니어도 부분 결과는 산다."""
    from daengs_evals.conversation_quality.judge import run_score
    from daengs_evals.conversation_quality.report import load_judgments

    case_a = _case(case_id="cq_case_a")
    case_b = _case(case_id="cq_case_b")
    rows = _lap_rows(case_a) + _lap_rows(case_b)
    calls = {"n": 0}

    def crashing_generate(**kw):
        calls["n"] += 1
        if calls["n"] > 2:  # case_a 의 두 축(모드·이어짐)을 다 돈 뒤 case_b 에서 죽는다
            raise KeyboardInterrupt
        return _fake_verdict(axis=kw["axis"])

    out = tmp_path / "judgments_crash.jsonl"
    with pytest.raises(KeyboardInterrupt):
        run_score(
            rows=rows,
            cases=[case_a, case_b],
            model=FAKE_JUDGE_MODEL,
            anchor_dir=_anchor_pass(tmp_path),
            anchors_sha256=FAKE_ANCHORS_SHA256,
            generate=crashing_generate,
            lap="crash",
            out_path=out,
        )

    _header, judgments = load_judgments(out)
    assert [j.case_id for j in judgments] == ["cq_case_a"]


def test_resume_skips_already_judged_rows_and_appends_the_rest(tmp_path):
    from daengs_evals.conversation_quality.judge import run_score
    from daengs_evals.conversation_quality.report import load_judgments

    case_a = _case(case_id="cq_case_a")
    case_b = _case(case_id="cq_case_b")
    rows = _lap_rows(case_a) + _lap_rows(case_b)
    out = tmp_path / "judgments_resume.jsonl"

    calls_first = {"n": 0}

    def first_pass(**kw):
        calls_first["n"] += 1
        if calls_first["n"] > 2:  # case_a 는 성공, case_b 는 매번 실패
            raise RuntimeError("일시적 오류라고 치자")
        return _fake_verdict(axis=kw["axis"])

    judgments_first = run_score(
        rows=rows,
        cases=[case_a, case_b],
        model=FAKE_JUDGE_MODEL,
        anchor_dir=_anchor_pass(tmp_path),
        anchors_sha256=FAKE_ANCHORS_SHA256,
        generate=first_pass,
        lap="resume",
        out_path=out,
    )
    assert [j.case_id for j in judgments_first] == ["cq_case_a"]

    second_calls: list[dict] = []

    def second_pass(**kw):
        second_calls.append(kw)
        return _fake_verdict(axis=kw["axis"])

    judgments_second = run_score(
        rows=rows,
        cases=[case_a, case_b],
        model=FAKE_JUDGE_MODEL,
        anchor_dir=_anchor_pass(tmp_path),
        anchors_sha256=FAKE_ANCHORS_SHA256,
        generate=second_pass,
        lap="resume",
        out_path=out,
        resume=True,
    )
    # case_a 는 이미 판정 파일에 있으니 다시 부르지 않는다 — case_b 의 두 축만 새로 불린다
    assert len(second_calls) == 2
    assert {j.case_id for j in judgments_second} == {"cq_case_a", "cq_case_b"}

    header, judgments = load_judgments(out)
    assert header["items"] == 2
    assert header["skipped"] == 0
    assert len(judgments) == 2


def test_resume_refuses_when_a_header_pin_disagrees(tmp_path):
    from daengs_evals.conversation_quality.judge import run_score

    case = _case()
    out = tmp_path / "judgments_pin.jsonl"
    run_score(
        rows=_lap_rows(case),
        cases=[case],
        model=FAKE_JUDGE_MODEL,
        anchor_dir=_anchor_pass(tmp_path),
        anchors_sha256=FAKE_ANCHORS_SHA256,
        generate=lambda **kw: _fake_verdict(axis=kw["axis"]),
        lap="pin",
        out_path=out,
    )
    other_model = "fake-judge-9999-99-99"
    # 옮겨진 핀 위에서 이어 돌리면 한 파일에 서로 다른 전제로 판정된 행이 섞인다 — 그게
    # 비교 게이트가 막으려는 바로 그 실패라 조용히 넘어가지 않고 SystemExit 으로 거부한다
    with pytest.raises(SystemExit, match="judge_model"):
        run_score(
            rows=_lap_rows(case),
            cases=[case],
            model=other_model,
            anchor_dir=_anchor_pass(tmp_path, model=other_model),
            anchors_sha256=FAKE_ANCHORS_SHA256,
            generate=lambda **kw: _fake_verdict(axis=kw["axis"]),
            lap="pin",
            out_path=out,
            resume=True,
        )


def test_a_single_failing_judge_call_is_recorded_and_the_loop_continues(tmp_path):
    import json

    from daengs_evals.conversation_quality.judge import run_score

    case = _repair_case()  # 세 축이 모두 적용되는 케이스

    def flaky(**kw):
        if kw["axis"] == "context_continuity":
            raise RuntimeError("일시적 5xx 라고 치자")
        return _fake_verdict(axis=kw["axis"])

    out = tmp_path / "judgments_flaky.jsonl"
    judgments = run_score(
        rows=_lap_rows(case),
        cases=[case],
        model=FAKE_JUDGE_MODEL,
        anchor_dir=_anchor_pass(tmp_path),
        anchors_sha256=FAKE_ANCHORS_SHA256,
        generate=flaky,
        lap="flaky",
        out_path=out,
    )
    # 축 하나만 실패해도 그 행 전체를 판정으로 안 남긴다 — 부분 판정이 완전한 판정처럼 보이면 안 된다
    assert judgments == []

    lines = [line for line in out.read_text("utf-8").splitlines() if line.strip()]
    header = json.loads(lines[0])
    assert header["skipped"] == 1
    body = [json.loads(line) for line in lines[1:]]
    assert len(body) == 1
    assert body[0]["type"] == "skip"
    assert body[0]["error_type"] == "RuntimeError"
    # 트레이스백도 프롬프트도 답변도 담지 않는다
    raw = lines[1]
    assert "일시적 5xx" not in raw
    assert case.turns[0].text not in raw
    assert "산책은 하루 두 번이 좋습니다" not in raw


def test_a_file_with_a_skipped_row_still_round_trips_through_report(tmp_path):
    """`load_judgments` → `summarize` → `render` 가 스킵 행이 섞인 파일에서도 죽지 않아야 한다."""
    from daengs_evals.conversation_quality.collect import load_lap, run_collect
    from daengs_evals.conversation_quality.drivers import FakeDriver
    from daengs_evals.conversation_quality.judge import PROMPT_VERSION, run_score
    from daengs_evals.conversation_quality.report import load_judgments, render, summarize

    case = _repair_case()
    lap_path = run_collect(
        cases=[case],
        driver=FakeDriver(replies=["산책은 하루 두 번이 좋습니다."]),
        out_dir=tmp_path,
        lap="rt",
        judge_model=FAKE_JUDGE_MODEL,
        prompt_version=PROMPT_VERSION,
        anchor_set="dev",
    )
    lap_meta, lap_rows = load_lap(lap_path)

    def flaky(**kw):
        if kw["axis"] == "repair_success":
            raise RuntimeError("boom")
        return _fake_verdict(axis=kw["axis"])

    out = tmp_path / "judgments_rt.jsonl"
    run_score(
        rows=lap_rows,
        cases=[case],
        model=FAKE_JUDGE_MODEL,
        anchor_dir=_anchor_pass(tmp_path),
        anchors_sha256=FAKE_ANCHORS_SHA256,
        generate=flaky,
        lap="rt",
        out_path=out,
    )
    judge_header, judgments = load_judgments(out)
    assert judgments == []
    summary = summarize(
        lap_meta=lap_meta, lap_rows=lap_rows, judge_header=judge_header, judgments=judgments
    )
    text = render(summary)
    assert "rt" in text


def test_judge_makes_no_live_call_at_import_time():
    import daengs_evals.conversation_quality.judge as m

    # provider 클라이언트도 API 키도 import 만으로는 필요 없어야 한다
    assert "openai" not in m.__dict__
    assert "settings" not in m.__dict__


# --- 앵커와 변이 (anchors.py) ---


def test_anchors_are_split_into_dev_and_holdout():
    from daengs_evals.conversation_quality.anchors import ANCHORS, ids

    assert set(ANCHORS) == {"dev", "holdout"}
    assert not (ids("dev") & ids("holdout"))


def test_both_directions_exist_for_the_two_axes_that_floor_at_zero():
    from daengs_evals.conversation_quality.anchors import ANCHORS

    # baseline 이 전부 0 이라 캘리브레이션은 앵커에서만 온다 — 성공 예시가 없으면
    # 판정기가 "항상 0" 이어도 통과한다
    for axis in ("context_continuity", "repair_success"):
        scores = {a.expected for a in ANCHORS["dev"] if a.axis == axis}
        assert 0 in scores and 2 in scores


def test_no_anchor_text_appears_in_any_axis_prompt():
    # 자기 앵커에 맞춰진 판정기는 아무것도 못 잰다 — 세 프롬프트 전부를 본다.
    from daengs_evals.conversation_quality import judge
    from daengs_evals.conversation_quality.anchors import ANCHORS

    prompts = judge.PROMPTS.values()
    for split in ANCHORS:
        for anchor in ANCHORS[split]:
            snippet = anchor.text[:30]
            assert not any(snippet in prompt for prompt in prompts), anchor.anchor_id


def test_every_axis_has_a_middle_band_anchor_in_dev():
    # 1 은 판정기가 도망갈 수 있는 자리다 — 0/2 만 있으면 그 도망이 안 걸린다
    from daengs_evals.conversation_quality.anchors import ANCHORS
    from daengs_evals.conversation_quality.judge import AXES

    for axis in AXES:
        scores = {a.expected for a in ANCHORS["dev"] if a.axis == axis}
        assert 1 in scores, axis


def test_a_needed_true_anchor_scores_high_without_asking_breaking_one_collinearity_direction():
    # 오늘 13 개 케이스는 need=True <-> ASK, need=False <-> ANSWER/REDIRECT 로 완전히
    # 겹친다 — 이 앵커가 그 방향 하나(need=True 인데 안 물어도 맞는 경우가 있다)를 깬다.
    # 역방향(need=False 인데 되묻는 것이 맞는 경우)은 `PROMPT_RESPONSE_MODE_FIT` 의 0점
    # 기준이 되묻기를 항상 0 으로 못박아서 프롬프트를 안 고치는 한 지을 수 없다 — 리뷰가
    # 그 방향은 만들지 않기로 정했다(`anchors.py` 모듈 docstring 참고).
    from daengs_evals.conversation_quality.anchors import ANCHORS

    rmf = [a for a in ANCHORS["dev"] if a.axis == "response_mode_fit"]
    needed_true_high_score = [
        a for a in rmf if a.payload["user_input_needed"] is True and a.expected == 2
    ]
    assert needed_true_high_score  # need=True 인데 되묻지 않은 답이 맞는 앵커가 있다


def test_context_continuity_pins_a_correct_decline_at_two():
    # 상태가 답에 영향이 없으면 «안 쓰는» 것이 옳다 — v3 프롬프트가 그렇게 말한다.
    # 이 자리가 0 으로 잘못 채점되면 판정기가 프롬프트를 안 따르는 것이다.
    from daengs_evals.conversation_quality.anchors import ANCHORS

    decline = next(a for a in ANCHORS["dev"] if a.anchor_id == "ctx_decline_correct")
    assert decline.expected == 2
    assert decline.axis == "context_continuity"
    assert "보더콜리" not in decline.payload["answer"]


def test_mutations_state_a_checkable_expected_direction():
    # 변이는 "정답이 바뀌어야 하는 최소 편집" 이다 — 방향을 못 말하면 변이가 아니라 잡음이다.
    from daengs_evals.conversation_quality.anchors import ANCHORS, mutation_pairs

    for split in ANCHORS:
        for base, mutated in mutation_pairs(split):
            assert base.axis == mutated.axis
            assert base.expected != mutated.expected
            assert mutated.edit  # 무엇을 바꿨는지 적혀 있어야 확인할 수 있다


def test_anchor_payloads_are_shaped_like_build_payload_output():
    # anchors.py 가 만든 payload 가 judge.build_prompt 를 실제로 통과해야 한다 —
    # 그래야 이 앵커가 진짜 판정기 입력과 같은 모양이라는 것이 보장된다.
    from daengs_evals.conversation_quality import judge
    from daengs_evals.conversation_quality.anchors import ANCHORS

    for split in ANCHORS:
        for anchor in ANCHORS[split]:
            prompt = judge.build_prompt(anchor.axis, anchor.payload)
            assert isinstance(prompt, str) and prompt.strip()


def test_anchor_check_produces_a_record_require_anchor_pass_accepts(tmp_path):
    from daengs_evals.conversation_quality import anchors, judge

    def all_correct(*, axis, prompt, payload, model):
        del prompt, model
        anchor = next(a for a in anchors.ANCHORS["dev"] if a.axis == axis and a.payload == payload)
        return _fake_verdict(score=anchor.expected, axis=axis)

    path = anchors.run_and_write(
        "dev", generate=all_correct, model=FAKE_JUDGE_MODEL, anchor_dir=tmp_path
    )
    record = judge.require_anchor_pass(
        tmp_path,
        anchor_set="dev",
        model=FAKE_JUDGE_MODEL,
        anchors_sha256=anchors.anchors_sha256(),
    )
    assert record["passed"] is True
    assert path.exists()


def test_anchor_check_fails_when_a_verdict_disagrees(tmp_path):
    from daengs_evals.conversation_quality import anchors, judge

    def always_zero(*, axis, prompt, payload, model):
        del prompt, payload, model
        return _fake_verdict(score=0, axis=axis)

    anchors.run_and_write("dev", generate=always_zero, model=FAKE_JUDGE_MODEL, anchor_dir=tmp_path)
    with pytest.raises(SystemExit):
        judge.require_anchor_pass(
            tmp_path,
            anchor_set="dev",
            model=FAKE_JUDGE_MODEL,
            anchors_sha256=anchors.anchors_sha256(),
        )


# --- 리포트와 전후 비교 (report.py) ---


def test_collect_score_report_round_trip_is_pinned(tmp_path):
    """collect → score → report 파일 이어달리기 전체를 한 번은 실제로 밟는다.

    `report.load_judgments` 를 부르는 테스트가 이전까지 없었고, `run_score` 출력을
    써서 다시 읽어 들이는 테스트도 없었다 — 판정 파일의 검증-합집합·헤더/판정 줄
    분리·`load_lap` → `summarize` 의 필드 이름이 전부 안 잡혀 있었다는 뜻이다.
    `TurnJudgment` · `JudgeHeader` 의 필드 하나가 이름이 바뀌어도 이 패키지의 유일한
    실제 사용 경로(하네스 그 자체)가 아니면 전체 스위트가 초록불일 수 있었다.

    대상 턴이 둘인 케이스를 써서 그 경로도 같이 덮는다.
    """
    from daengs_evals.conversation_quality.collect import load_lap, run_collect
    from daengs_evals.conversation_quality.drivers import FakeDriver
    from daengs_evals.conversation_quality.judge import PROMPT_VERSION, run_score
    from daengs_evals.conversation_quality.report import (
        load_judgments,
        render,
        render_compare,
        summarize,
    )

    case = _case(
        turns=[
            Turn(role="user", text="오늘 건강 상태는 어때?"),
            Turn(role="assistant", text="증상의 원인이나 병명은 여기서 판단하지 않아요."),
            Turn(role="user", text="오늘 힘이 없어 보이는데?"),
            Turn(role="assistant", text="식욕이나 배변 상태 등 다른 변화가 있는지 관찰해 주세요."),
        ],
        target_turns=[1, 3],
    )

    def _run_lap(lap: str, replies: list[str]):
        lap_path = run_collect(
            cases=[case],
            driver=FakeDriver(replies=list(replies)),
            out_dir=tmp_path,
            lap=lap,
            judge_model=FAKE_JUDGE_MODEL,
            prompt_version=PROMPT_VERSION,
            anchor_set="dev",
        )
        lap_meta, lap_rows = load_lap(lap_path)
        judgments_path = tmp_path / f"judgments_{lap}.jsonl"
        run_score(
            rows=lap_rows,
            cases=[case],
            model=FAKE_JUDGE_MODEL,
            anchor_dir=_anchor_pass(tmp_path),
            anchors_sha256=FAKE_ANCHORS_SHA256,
            generate=lambda **kw: _fake_verdict(axis=kw["axis"]),
            lap=lap,
            out_path=judgments_path,
        )
        judge_header, judgments = load_judgments(judgments_path)
        return summarize(
            lap_meta=lap_meta, lap_rows=lap_rows, judge_header=judge_header, judgments=judgments
        )

    before = _run_lap("t1", ["첫 답 before", "둘째 답 before"])
    after = _run_lap("t2", ["첫 답 after", "둘째 답 after"])

    assert before.n_turns_judged == 2
    assert after.n_turns_judged == 2

    report_text = render(before)
    assert "t1" in report_text

    compare_text = render_compare(before=before, after=after)
    assert "t1" in compare_text and "t2" in compare_text


def _axis_stats(**over):
    from daengs_evals.conversation_quality.report import AxisStat

    base = {
        "response_mode_fit": AxisStat(n=10, mean=1.4, distribution={0: 2, 1: 3, 2: 5}),
        "context_continuity": AxisStat(n=8, mean=0.0, distribution={0: 8}),
        "repair_success": AxisStat(n=3, mean=0.0, distribution={0: 3}),
    }
    base.update(over)
    return base


def _summary(**over):
    from daengs_evals.conversation_quality.report import (
        StateAuditTally,
        Summary,
        UnmeasuredTally,
        UsabilityTally,
    )

    base = {
        "lap": "lap1",
        "cases_sha256": "a" * 64,
        "judge_model": FAKE_JUDGE_MODEL,
        "prompt_version": 3,
        "anchor_set": "dev",
        "adapter_mode": "fake",
        "n_turns_total": 13,
        "n_turns_judged": 13,
        "axis_stats": _axis_stats(),
        "usability": UsabilityTally(
            usable=8,
            unusable_safety=0,
            unusable_response_mode_fit=2,
            unusable_repair_success=3,
        ),
        "state_audit": StateAuditTally(
            n_audited=8,
            relevant_state_available=5,
            relevant_state_used=3,
            state_used_correctly=2,
            unsupported_or_superficial_personalization=1,
        ),
        "unmeasured": UnmeasuredTally(
            numerator=9,
            denominator=39,
            excluded_before_judging_slots=3,
            fake_adapter_slots=3,
            not_applicable_slots=3,
        ),
        "dead_end_count": 2,
        "dead_end_n": 13,
    }
    base.update(over)
    return Summary(**base)


def test_report_is_deterministic():
    from daengs_evals.conversation_quality.report import render

    summary = _summary()
    assert render(summary) == render(summary)


def test_report_never_prints_a_combined_score():
    from daengs_evals.conversation_quality.report import render

    text = render(_summary())
    assert "종합" not in text and "총점" not in text


def test_report_states_the_unmeasured_ratio():
    from daengs_evals.conversation_quality.report import render

    assert "미측정" in render(_summary())


def test_report_labels_dead_end_as_a_diagnostic_not_an_axis():
    from daengs_evals.conversation_quality.report import render

    text = render(_summary())
    assert "dead_end" in text
    assert "진단" in text


def test_report_reports_state_audit_as_facts_not_scores():
    from daengs_evals.conversation_quality.report import render

    text = render(_summary())
    assert "relevant_state_available" in text
    assert "사실" in text


def test_report_breaks_down_the_usability_gate_by_reason():
    from daengs_evals.conversation_quality.report import render

    text = render(_summary())
    assert "safety" in text
    assert "response_mode_fit" in text
    assert "repair_success" in text


def test_report_states_all_three_axes_are_not_calibrated():
    from daengs_evals.conversation_quality.report import render

    text = render(_summary())
    assert "not_calibrated" in text


def test_compare_labels_the_before_column_as_feature_absent_for_floored_axes():
    from daengs_evals.conversation_quality.report import AxisStat, render_compare

    before = _summary(
        axis_stats=_axis_stats(context_continuity=AxisStat(n=8, mean=0.0, distribution={0: 8}))
    )
    after = _summary(
        axis_stats=_axis_stats(
            context_continuity=AxisStat(n=8, mean=1.7, distribution={1: 3, 2: 5})
        )
    )
    # 0 -> 1.7 을 "모델이 좋아졌다" 로 읽히게 두지 않는다
    text = render_compare(before=before, after=after)
    assert "기능 부재" in text


def test_compare_labels_response_mode_fit_as_a_genuine_before_after_column():
    from daengs_evals.conversation_quality.report import render_compare

    before = _summary()
    after = _summary(
        lap="lap2", axis_stats=_axis_stats(response_mode_fit=_axis_stats()["response_mode_fit"])
    )
    text = render_compare(before=before, after=after)
    # 실제 변량이 있는 축은 "기능 부재" 라벨을 달지 않는다
    rmf_line = next(
        line for line in text.splitlines() if "response_mode_fit" in line and "|" in line
    )
    assert "기능 부재" not in rmf_line


@pytest.mark.parametrize(
    "field,new_value",
    [
        ("cases_sha256", "c" * 64),
        ("judge_model", "different-model"),
        ("prompt_version", 4),
        ("anchor_set", "holdout"),
        ("adapter_mode", "real"),
    ],
)
def test_compare_refuses_when_a_pinned_thing_moved(field, new_value):
    from daengs_evals.conversation_quality.report import render_compare

    before = _summary()
    after = _summary(lap="lap2", **{field: new_value})
    with pytest.raises(ValueError, match=field):
        render_compare(before=before, after=after)


def test_compare_does_not_refuse_when_only_the_lap_label_differs():
    from daengs_evals.conversation_quality.report import render_compare

    before = _summary()
    after = _summary(lap="lap2")
    # 랩 라벨은 고정 다섯에 안 든다 - 이것까지 막으면 애초에 비교할 것이 없다
    render_compare(before=before, after=after)


def test_compare_refuses_a_fake_driver_lap_against_a_fake_lap():
    from daengs_evals.conversation_quality.report import render_compare

    # 오케스트레이터를 아예 안 돌린 합성 랩(`fake-driver`)과 진짜 오케스트레이터 +
    # 가짜 capability 어댑터로 돌린 랩(`fake`)은 두 랩 사이의 가장 큰 차이인데, 예전에는
    # 둘 다 헤더에 `"fake"` 를 적어 이 비교가 조용히 허락됐다.
    before = _summary(adapter_mode="fake-driver")
    after = _summary(lap="lap2", adapter_mode="fake")
    with pytest.raises(ValueError, match="adapter_mode"):
        render_compare(before=before, after=after)


def test_summarize_excludes_fake_adapter_rows_from_axis_stats_but_counts_them_unmeasured(
    tmp_path,
):
    from daengs_evals.conversation_quality.judge import run_score
    from daengs_evals.conversation_quality.report import summarize

    case = _repair_case()
    row = {
        "case_id": case.case_id,
        "turn_index": 3,
        "query": case.turns[2].text,
        "message": "산책은 하루 두 번이 좋습니다.",
        "state_supplied": {},
        "answered_by_fake_adapter": True,
        "adapter_mode": "fallback-only",
    }
    judgments = run_score(
        rows=[row],
        cases=[case],
        model=FAKE_JUDGE_MODEL,
        anchor_dir=_anchor_pass(tmp_path),
        anchors_sha256=FAKE_ANCHORS_SHA256,
        generate=lambda **kw: _fake_verdict(score=2, axis=kw["axis"]),
    )
    lap_meta = {"lap": "t1", "cases_sha256": "a" * 64, "adapter_mode": "fallback-only"}
    judge_header = {
        "judge_model": FAKE_JUDGE_MODEL,
        "prompt_version": 3,
        "anchor_set": "dev",
        "skipped": 0,
    }
    summary = summarize(
        lap_meta=lap_meta, lap_rows=[row], judge_header=judge_header, judgments=judgments
    )
    assert all(stat.n == 0 for stat in summary.axis_stats.values())
    assert summary.unmeasured.numerator == summary.unmeasured.denominator
    assert summary.unmeasured.fake_adapter_slots == summary.unmeasured.numerator


def test_summarize_refuses_when_lap_and_judge_header_disagree_on_judge_model():
    from daengs_evals.conversation_quality.report import summarize

    # `score --judge-model X` 를 `judge_model=Y` 라고 적힌 랩에 대고 돌리면, 판정 파일은
    # X 를 정직하게 적지만 아무것도 그것이 랩의 계획과 어긋났다고 말해 주지 않았다 —
    # 리포트는 조용히 X 를 보여줬다. 이제는 여기서 거부해야 한다.
    lap_meta = {
        "lap": "t1",
        "cases_sha256": "a" * 64,
        "adapter_mode": "real",
        "judge_model": "declared-model",
        "prompt_version": 3,
        "anchor_set": "dev",
    }
    judge_header = {
        "judge_model": "actually-used-model",
        "prompt_version": 3,
        "anchor_set": "dev",
        "skipped": 0,
    }
    with pytest.raises(ValueError, match="judge_model"):
        summarize(lap_meta=lap_meta, lap_rows=[], judge_header=judge_header, judgments=[])


def test_summarize_allows_a_lap_that_does_not_declare_the_shared_pins():
    from daengs_evals.conversation_quality.report import summarize

    # 손으로 만든 랩 메타(테스트 fixture, 옛 랩)는 judge_model 등을 아예 안 적었을 수 있다 —
    # 없음과 다름은 다르다. 여기서는 거부하지 않는다.
    lap_meta = {"lap": "t1", "cases_sha256": "a" * 64, "adapter_mode": "real"}
    judge_header = {
        "judge_model": FAKE_JUDGE_MODEL,
        "prompt_version": 3,
        "anchor_set": "dev",
        "skipped": 0,
    }
    summarize(lap_meta=lap_meta, lap_rows=[], judge_header=judge_header, judgments=[])


def test_summarize_reports_code_checks_per_case():
    from daengs_evals.conversation_quality.report import summarize

    lap_rows = [
        {
            "case_id": "cq_a",
            "turn_index": 1,
            "message": "같은 답",
            "answered_by_fake_adapter": False,
        },
        {
            "case_id": "cq_a",
            "turn_index": 3,
            "message": "같은 답",
            "answered_by_fake_adapter": False,
        },
        {
            "case_id": "cq_b",
            "turn_index": 1,
            "message": "다른 답",
            "answered_by_fake_adapter": False,
        },
    ]
    lap_meta = {"lap": "t1", "cases_sha256": "a" * 64, "adapter_mode": "real"}
    judge_header = {
        "judge_model": FAKE_JUDGE_MODEL,
        "prompt_version": 3,
        "anchor_set": "dev",
        "skipped": 0,
    }
    summary = summarize(
        lap_meta=lap_meta, lap_rows=lap_rows, judge_header=judge_header, judgments=[]
    )
    assert summary.code_checks_measured is True
    assert summary.code_checks["cq_a"].max_repeat_count == 2
    assert summary.code_checks["cq_b"].max_repeat_count == 1


def test_summarize_reports_dead_end_and_code_checks_as_unmeasured_without_settings(monkeypatch):
    import daengs_evals.conversation_quality.report as report_mod

    # `report`·`compare` 는 "이미 있는 파일만 읽는다"는 약속이다 — backend 설정
    # (DB 접속 정보 · 암호화 키)이 없는 체크아웃에서도 죽지 않고 미측정으로 내려야 한다.
    def _boom():
        raise RuntimeError("settings 없음")

    monkeypatch.setattr(report_mod, "_fixed_refusals", _boom)

    lap_rows = [
        {"case_id": "cq_a", "turn_index": 1, "message": "답", "answered_by_fake_adapter": False}
    ]
    judgment = _judgment(response_mode_fit=2)
    lap_meta = {"lap": "t1", "cases_sha256": "a" * 64, "adapter_mode": "real"}
    judge_header = {
        "judge_model": FAKE_JUDGE_MODEL,
        "prompt_version": 3,
        "anchor_set": "dev",
        "skipped": 0,
    }
    summary = report_mod.summarize(
        lap_meta=lap_meta, lap_rows=lap_rows, judge_header=judge_header, judgments=[judgment]
    )
    assert summary.dead_end_measured is False
    assert summary.code_checks_measured is False
    assert summary.code_checks == {}
    # 렌더도 죽지 않고 미측정임을 말해야 한다
    text = report_mod.render(summary)
    assert "측정 불가" in text


def test_summarize_counts_rows_excluded_before_judging_as_unmeasured():
    from daengs_evals.conversation_quality.report import summarize

    lap_rows = [
        {"case_id": "cq_a", "turn_index": 1, "answered_by_fake_adapter": False},
        {"case_id": "cq_b", "turn_index": 1, "answered_by_fake_adapter": False},
    ]
    judge_header = {
        "judge_model": FAKE_JUDGE_MODEL,
        "prompt_version": 3,
        "anchor_set": "dev",
        "skipped": 2,
    }
    lap_meta = {"lap": "t1", "cases_sha256": "a" * 64, "adapter_mode": "real"}
    summary = summarize(
        lap_meta=lap_meta, lap_rows=lap_rows, judge_header=judge_header, judgments=[]
    )
    assert summary.unmeasured.denominator == 6
    assert summary.unmeasured.numerator == 6
    assert summary.unmeasured.excluded_before_judging_slots == 6


def _judgment(case_id="cq_x", turn_index=1, response_mode_fit=2, not_applicable=None):
    from daengs_evals.conversation_quality.judge import TurnJudgment
    from daengs_evals.conversation_quality.rubric import AxisScores

    return TurnJudgment(
        case_id=case_id,
        turn_index=turn_index,
        scores=AxisScores(response_mode_fit=response_mode_fit),
        verdicts={},
        not_applicable=not_applicable or ["context_continuity", "repair_success"],
    )


def test_dead_end_catches_a_fixed_redirect_that_still_scored_well_on_mode():
    """이 변경의 요점 — `response_mode_fit` 이 실패로 안 잡는 막다른 길을 잡는다."""
    from daengs_backend.orchestration.redirects import SCOPED_REDIRECT_MESSAGES
    from daengs_evals.conversation_quality.report import summarize

    fixed_text = SCOPED_REDIRECT_MESSAGES["off_topic"]
    row = {
        "case_id": "cq_x",
        "turn_index": 1,
        "message": fixed_text,
        "answered_by_fake_adapter": False,
    }
    judgment = _judgment(response_mode_fit=2)  # 모드는 통과 판정
    lap_meta = {"lap": "t1", "cases_sha256": "a" * 64, "adapter_mode": "real"}
    judge_header = {
        "judge_model": FAKE_JUDGE_MODEL,
        "prompt_version": 3,
        "anchor_set": "dev",
        "skipped": 0,
    }
    summary = summarize(
        lap_meta=lap_meta, lap_rows=[row], judge_header=judge_header, judgments=[judgment]
    )
    assert summary.dead_end_count == 1
    assert summary.dead_end_n == 1


def test_dead_end_does_not_flag_a_non_fixed_answer_even_at_a_perfect_mode_score():
    """부분 신호라는 것을 확인한다 — 고정 문구가 아니면 이 진단으로는 안 잡힌다."""
    from daengs_evals.conversation_quality.report import summarize

    row = {
        "case_id": "cq_x",
        "turn_index": 1,
        "message": "산책은 하루 두 번이 좋습니다.",
        "answered_by_fake_adapter": False,
    }
    judgment = _judgment(response_mode_fit=2)
    lap_meta = {"lap": "t1", "cases_sha256": "a" * 64, "adapter_mode": "real"}
    judge_header = {
        "judge_model": FAKE_JUDGE_MODEL,
        "prompt_version": 3,
        "anchor_set": "dev",
        "skipped": 0,
    }
    summary = summarize(
        lap_meta=lap_meta, lap_rows=[row], judge_header=judge_header, judgments=[judgment]
    )
    assert summary.dead_end_count == 0
    assert summary.dead_end_n == 1


def test_dead_end_does_not_double_count_a_response_mode_fit_failure():
    """`response_mode_fit == 0` 인 고정 문구 답은 이미 `unusable_response_mode_fit` 이 잡는다 —
    같은 실패를 `dead_end` 로 다시 세지 않는다."""
    from daengs_backend.orchestration.redirects import SCOPED_REDIRECT_MESSAGES
    from daengs_evals.conversation_quality.report import summarize

    fixed_text = SCOPED_REDIRECT_MESSAGES["off_topic"]
    row = {
        "case_id": "cq_x",
        "turn_index": 1,
        "message": fixed_text,
        "answered_by_fake_adapter": False,
    }
    judgment = _judgment(response_mode_fit=0)
    lap_meta = {"lap": "t1", "cases_sha256": "a" * 64, "adapter_mode": "real"}
    judge_header = {
        "judge_model": FAKE_JUDGE_MODEL,
        "prompt_version": 3,
        "anchor_set": "dev",
        "skipped": 0,
    }
    summary = summarize(
        lap_meta=lap_meta, lap_rows=[row], judge_header=judge_header, judgments=[judgment]
    )
    assert summary.dead_end_count == 0
    assert summary.usability.unusable_response_mode_fit == 1


def test_cli_score_computes_the_anchor_hash_and_passes_it_as_a_required_keyword(
    tmp_path, monkeypatch
):
    """CLI 가 `anchors.anchors_sha256()` 을 스스로 내어 `run_score` 에 넘기는지 본다.
    **실제 judge 를 부르지 않는다** — `run_score` 를 얇은 스텁으로 갈아 끼운다."""
    import json

    from daengs_evals.conversation_quality import __main__ as cli_mod
    from daengs_evals.conversation_quality import anchors as anchors_mod

    lap_path = tmp_path / "lap_t1.jsonl"
    lap_path.write_text(
        json.dumps({"kind": "meta", "lap": "t1", "cases_sha256": "a" * 64})
        + "\n"
        + json.dumps(
            {"kind": "turn", "case_id": "cq_wellness_vague_01", "turn_index": 1, "message": "x"}
        )
        + "\n",
        encoding="utf-8",
    )
    captured = {}

    def fake_run_score(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(cli_mod, "run_score", fake_run_score)
    monkeypatch.setattr(cli_mod, "load_cases", lambda path: [_case()])

    parser = cli_mod.build_parser()
    args = parser.parse_args(
        [
            "score",
            "--lap-file",
            str(lap_path),
            "--judge-model",
            FAKE_JUDGE_MODEL,
            "--out",
            str(tmp_path / "judgments_t1.jsonl"),
        ]
    )
    args.func(args)

    assert "anchors_sha256" in captured
    assert captured["anchors_sha256"] == anchors_mod.anchors_sha256()


def test_every_module_in_the_package_imports_without_backend_settings():
    """`transcript.py` · `report.py` 둘 다 겪었던 결함의 재발 방지.

    **같은 프로세스 테스트로는 못 잡는다** — `conftest.py` 가 `DAENGS_*` 를 이미 채워
    뒀고, 그 시점에 `sys.modules` 에 `daengs_backend.config` 가 이미 캐시돼 있을 수도
    있다. 그래서 하위 프로세스를 새로 띄우고 이 패키지의 모듈을 하나씩 `import` 한다 —
    판정기·오케스트레이터를 부르는 것이 아니라 **import 만 해도** 죽는지를 본다.

    **`DAENGS_*` 환경 변수를 지우는 것만으로는 이 속성을 못 잡는다.** `daengs_backend.
    config.ENV_FILE` 이 절대 경로라, 이 워크트리에 `backend/.env` 가 실제로 있으면
    환경 변수를 아무리 지워도 `pydantic-settings` 가 그 파일에서 값을 읽어 설정 로딩에
    성공해 버린다 — 이 테스트가 예전에 통과했던 것은 마침 이 워크트리에 `.env` 가
    없었기 때문이지, 무엇을 검사했기 때문이 아니다. 그래서 증상(프로세스가 죽었는가)
    대신 **속성 자체**(`daengs_backend.config` 가 `sys.modules` 에 들어왔는가)를
    바로 잰다 — `.env` 가 있는 머신에서도, 없는 머신에서도 같은 것을 검사한다.
    """
    import pkgutil
    import subprocess
    import sys

    import daengs_evals.conversation_quality as pkg

    module_names = [
        info.name for info in pkgutil.iter_modules(pkg.__path__, prefix=f"{pkg.__name__}.")
    ]
    # 패키지가 비었으면 이 테스트는 아무것도 안 잰 것이다 — 그 자체가 실패여야 한다.
    assert module_names

    failures: dict[str, str] = {}
    for name in module_names:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    f"import sys, {name}; assert 'daengs_backend.config' not in sys.modules, "
                    "sorted(m for m in sys.modules if m.startswith('daengs_backend'))"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            failures[name] = result.stderr.strip().splitlines()[-1] if result.stderr else ""

    assert not failures, (
        "다음 모듈을 import 만 했는데 daengs_backend.config 가 로딩됐습니다"
        f" (backend 설정을 최상단에서 물었다는 뜻입니다): {failures}"
    )
