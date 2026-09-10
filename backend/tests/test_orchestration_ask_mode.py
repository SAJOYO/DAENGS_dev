"""미명세 질문에 되묻는 응답 모드 (#415) — 결정론 부분만 잰다. 프로바이더 호출 0.

`#401` 의 before 랩이 이 카드의 자리를 정확히 짚었다: `오늘 건강 상태는 어때?` 에서
**라우터는 `general` 을 제대로 골랐고**, 오분류는 General 능력 **안에서** 났다
(`{kind: refuse, reason: diagnosis}` → `REFUSED`). 그래서 이 파일이 보는 것은
`adapters/general.py` 와 `aggregate.py` 둘뿐이다 — `planner.py` · `semantic.py` 는 범위 밖이다.

계약은 ①′(D-068): **`AssistantStatus` 도 `CapabilityStatus` 도 안 넓힌다.** General 이
`kind="ask"` 를 내면 어댑터가 그것을 `ClarifyRequest` 모양으로 `data["ask"]` 에 담고,
`aggregate` 가 **general 단독일 때만** 이미 있는 `CLARIFY` 로 옮긴다. `RoutePlan.clarify` 는
끝까지 안 건드리므로 배타성 불변식(`clarify_is_exclusive` · `graph._validate_route_plan`)도
그대로다 — 이 파일의 마지막 절이 그 둘을 회귀로 잡는다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daengs_backend.orchestration.adapters.general import (
    GENERAL_ASK_MISSING,
    GENERAL_CARE_LOG_PROMPT_VERSION,
    GENERAL_CARE_LOG_VET_PROMPT_VERSION,
    GENERAL_PROMPT_VERSION,
    GENERAL_VET_PROMPT_VERSION,
    GeneralAnswer,
    GeneralCapabilityAdapter,
    build_general_prompt,
    validate_general_answer,
)
from daengs_backend.orchestration.aggregate import aggregate_results
from daengs_backend.orchestration.contracts import (
    AssistantStatus,
    CapabilityName,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    CareLogContext,
    GeneralPayload,
    RoutePlan,
    RouterKind,
)
from daengs_backend.orchestration.planner import _payload_for
from daengs_evals.conversation_quality.cases import load_cases

pytestmark = pytest.mark.anyio

QUERY = "오늘 건강 상태는 어때?"
ASK = "오늘 평소와 달라 보이는 점이 있나요?"
CARE_LOG = CareLogContext(
    day="2026-09-10", meal=2, medication=0, snack=0, walk=1, last_meal_at="18:30"
)


# ── 모델 출력 스키마: 세 번째 kind ────────────────────────────────────


def test_answer_schema_has_a_third_kind_for_asking_back() -> None:
    """`ask` 가 스키마에 없으면 제약 디코딩이 그 출력을 아예 못 낸다.

    `kind` 는 `Literal` 이라 JSON 스키마의 enum 으로 나간다 — 값을 안 더하면 모델은
    되묻고 싶어도 `answer` 나 `refuse` 중 하나로 눌러 담는다. before 랩이 본 것이 그것이다.
    """
    kinds = GeneralAnswer.model_json_schema()["properties"]["kind"]["enum"]
    assert sorted(kinds) == ["answer", "ask", "refuse"]


# ── 어댑터: 모델 출력 → CapabilityResult ──────────────────────────────


def general_request(context: dict | None = None) -> CapabilityRequest:
    return CapabilityRequest(
        capability="general",
        payload=_payload_for("general", query=QUERY, context=context or {}),
    )


class Transport:
    def __init__(self, output: object) -> None:
        self.output = output
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> object:
        self.prompts.append(prompt)
        return self.output


async def run_adapter(output: object) -> CapabilityResult:
    return await GeneralCapabilityAdapter(generate=Transport(output)).run(
        general_request(), request_id="r1"
    )


async def test_adapter_carries_an_ask_as_a_clarify_shaped_result() -> None:
    """`CapabilityStatus` 는 안 넓힌다 (D-068).

    능력 수준의 `OK` 는 "능력이 돌아서 자기 출력을 냈다" 는 뜻이고, 그 출력이 질문인지
    답인지는 `data` 의 모양이 말한다 — `data` 는 원래 능력마다 다른 자리다(walk 는 `now`,
    general 은 `answer`). 턴이 답해졌는지는 `AssistantStatus` 가 말한다.
    """
    result = await run_adapter(
        json.dumps({"kind": "ask", "text": "", "question": f"  {ASK} ", "reason": None})
    )
    assert result.status == CapabilityStatus.OK
    assert result.data == {"ask": {"question": ASK, "missing": [GENERAL_ASK_MISSING]}}
    assert result.capability == CapabilityName.GENERAL and result.elapsed_ms >= 0


async def test_adapter_rejects_an_ask_that_the_clarify_contract_cannot_hold() -> None:
    """`ClarifyRequest.question` 은 500자다. 어댑터가 그 자리에서 걸러야 집계가 안 터진다."""
    result = await run_adapter(
        json.dumps({"kind": "ask", "text": "", "question": "가" * 501, "reason": None})
    )
    assert result.status == CapabilityStatus.ERROR
    assert result.error is not None and result.error.kind == "general_invalid_output"
    assert "가가가" not in result.error.detail


# ── 집계: general 단독 ask → CLARIFY ──────────────────────────────────


def plan(*capabilities: str) -> RoutePlan:
    return RoutePlan(
        requests=[
            CapabilityRequest(capability=name, payload=_payload_for(name, query=QUERY, context={}))
            for name in capabilities
        ],
        router=RouterKind.LLM,
        model="gemini-3.1-flash-lite",
        prompt_version="semantic-router-ko-v10",
    )


def ask_result(question: str = ASK) -> CapabilityResult:
    return CapabilityResult(
        capability=CapabilityName.GENERAL,
        status=CapabilityStatus.OK,
        data={"ask": {"question": question, "missing": [GENERAL_ASK_MISSING]}},
        elapsed_ms=1,
    )


def answer_result(answer: str = "보통 2~4주에 한 번이면 돼요.") -> CapabilityResult:
    return CapabilityResult(
        capability=CapabilityName.GENERAL,
        status=CapabilityStatus.OK,
        data={"answer": answer},
        elapsed_ms=1,
    )


def test_a_lone_general_ask_becomes_clarify() -> None:
    response = aggregate_results(
        request_id="r1", route_plan=plan("general"), results=[ask_result()]
    )
    assert response.status == AssistantStatus.CLARIFY
    assert response.message == ASK
    assert response.clarify is not None
    assert response.clarify.question == ASK
    assert response.clarify.missing == [GENERAL_ASK_MISSING]


def test_the_ask_response_keeps_clarify_exclusive_from_the_client_side() -> None:
    """진리표의 `CLARIFY → 아무것도 실행되지 않았음` 을 클라이언트 쪽에서 그대로 지킨다.

    General 이 실제로 돌았다는 사실은 `route` 트레이스와 `#401` 하네스의 `general_decision`
    에 남는다 — 응답에는 안 붙인다. 이것이 D-068 이 ①′ 를 고르며 치른 값이다.
    """
    response = aggregate_results(
        request_id="r1", route_plan=plan("general"), results=[ask_result()]
    )
    assert response.results == []
    assert response.handoffs == []


def test_a_general_answer_is_still_answered() -> None:
    response = aggregate_results(
        request_id="r1", route_plan=plan("general"), results=[answer_result()]
    )
    assert response.status == AssistantStatus.ANSWERED
    assert response.clarify is None


def test_an_ask_next_to_another_capability_is_not_mapped() -> None:
    """폴백은 규칙상 단독으로만 조립된다(`planner.py`). 그래도 거는 벨트 앤 브레이시스 —
    나중에 그 규칙이 풀려도 되묻기가 다른 능력의 답을 조용히 삼키지 않는다."""
    training = CapabilityResult(
        capability=CapabilityName.TRAINING,
        status=CapabilityStatus.OK,
        data={"answer": "훈련 답"},
        elapsed_ms=1,
    )
    response = aggregate_results(
        request_id="r1",
        route_plan=plan("training", "general"),
        results=[training, ask_result()],
    )
    assert response.status != AssistantStatus.CLARIFY
    assert response.clarify is None


def test_an_ask_shaped_payload_from_another_capability_is_not_mapped() -> None:
    """되묻기는 General 만의 출력이다 — `data` 에 `ask` 키를 쓴 다른 능력이 생겨도
    그것이 대화 계약을 바꾸지 못한다."""
    other = CapabilityResult(
        capability=CapabilityName.TRAINING,
        status=CapabilityStatus.OK,
        data={"ask": {"question": ASK, "missing": [GENERAL_ASK_MISSING]}},
        elapsed_ms=1,
    )
    response = aggregate_results(request_id="r1", route_plan=plan("training"), results=[other])
    assert response.status != AssistantStatus.CLARIFY


def test_plan_time_clarify_still_wins_and_stays_exclusive() -> None:
    """좌표 누락 CLARIFY 회귀. 생산자가 둘이 됐어도 계획 시점 것이 먼저다."""
    coordinate_plan = RoutePlan(
        clarify={"question": "현재 위치의 위도를 알려주세요.", "missing": ["location.lat"]},
        router=RouterKind.LLM,
    )
    response = aggregate_results(request_id="r1", route_plan=coordinate_plan, results=[])
    assert response.status == AssistantStatus.CLARIFY
    assert response.clarify is not None and response.clarify.missing == ["location.lat"]


def test_the_ask_never_touches_the_route_plan() -> None:
    """`RoutePlan.clarify` 를 사후에 채우는 길은 만들지 않았다 — 배타성 검증기 둘이 그대로 선다."""
    route_plan = plan("general")
    aggregate_results(request_id="r1", route_plan=route_plan, results=[ask_result()])
    assert route_plan.clarify is None


# ── 프롬프트: 무엇을 되묻고 무엇은 안 되묻나 ─────────────────────────


def test_prompt_versions_move_with_the_schema_to_v6() -> None:
    """**`kind` 를 넓히면 v3 본문이 조용히 바뀐다.**

    `build_general_prompt` 는 `GeneralAnswer.model_json_schema()` 를 v3 리터럴 가지에도
    끼워 넣는다. `ask` 를 더하면 그 스키마 문자열이 달라지므로, 버전 문자열을 그대로 두면
    D-057 ③ 이 84건 쌍대 비교로 승인한 본문이 같은 이름으로 다른 물건이 된다. 네 조합의
    버전을 함께 올리는 것이 그 드리프트를 막는 유일한 자리다.
    """
    assert GENERAL_PROMPT_VERSION == "general-answer-ko-v6"
    assert GENERAL_CARE_LOG_PROMPT_VERSION == "general-answer-ko-v6-carelog"
    assert GENERAL_VET_PROMPT_VERSION == "general-answer-ko-v6-vetspend"
    assert GENERAL_CARE_LOG_VET_PROMPT_VERSION == "general-answer-ko-v6-carelog-vetspend"


def test_prompt_allows_related_axes_in_one_question_but_not_an_intake_interview() -> None:
    """승인된 되묻기 모양 (D-068).

    **"관찰 축 하나만" 이 아니다.** 식욕 · 활력 · 배변 · 구토/설사 · 호흡처럼 서로 붙어
    있는 항목은 한 문장에 묶어도 되고, 대신 사용자가 전부 답하게 요구하지 않는다 —
    "가장 눈에 띄는 것부터" 다. 금지되는 것은 짧은 질문을 여러 턴에 걸쳐 던지는 문진이다.
    """
    prompt = build_general_prompt(GeneralPayload(question=QUERY))
    assert "ask: " in prompt
    assert "One question per turn." in prompt
    assert "식욕 · 활력 · 배변 · 구토/설사 · 호흡" in prompt
    assert "start with whatever stands out most" in prompt
    assert "Never demand that they answer every item" in prompt
    assert "never spread the items across several turns as an intake interview" in prompt
    # 판단은 어느 쪽으로도 안 붙인다.
    assert "Never list candidate diseases" in prompt
    assert "never say the dog is healthy, fine, normal, or lacking anything" in prompt


def test_prompt_narrows_the_two_rules_that_closed_the_observed_turns() -> None:
    """before 랩의 오분류 둘을 규칙에서 직접 막는다.

    `diagnosis` 5건은 관찰을 하나도 안 적은 상태 질문이었고, `emergency` 1건
    (`cq_symptom_missing_triage_01`)은 반복 구토였다 — 둘 다 원래 거절 목록이 아니다.
    """
    prompt = build_general_prompt(GeneralPayload(question=QUERY))
    assert "names no observation is an ask, not a diagnosis refusal" in prompt
    assert "Vomiting, diarrhea, limping, and appetite loss are not on this list" in prompt
    # v3 가 이미 세워 둔 경계는 그대로다 — 병명을 콕 집어 묻는 것은 여전히 거절이다.
    assert "explicitly asks for a disease name" in prompt
    assert 'Say nothing beyond "go to a veterinary hospital right now"' in prompt


# ── 수용 집합: #401 케이스 파일이 이 카드의 대상을 정한다 ────────────

CASES = Path(__file__).resolve().parents[1] / "evals" / "conversation_quality" / "cases_v1.jsonl"


def test_the_acceptance_set_is_the_six_turns_the_before_lap_closed() -> None:
    """카드 본문의 "6개가 움직여야 한다" 를 케이스 파일에서 다시 센다.

    본문은 그 여섯을 전부 `diagnosis` 라고 적었지만 before 랩은 다르다 — 다섯이
    `diagnosis` 이고 하나(`cq_symptom_missing_triage_01`)는 `emergency` 다. 프롬프트를
    고칠 때 규칙 **둘**을 좁혀야 하는 이유가 그것이고, 이 테스트가 그 수를 고정한다.
    케이스 파일이 바뀌면 여기가 먼저 깨져서 수용 집합을 다시 보게 된다.
    """
    cases = {case.case_id: case for case in load_cases(CASES)}
    ask_cases = sorted(cid for cid, case in cases.items() if case.expected_mode == "ASK")
    assert ask_cases == [
        "cq_correction_explicit_01",
        "cq_observed_wellness_repair_01",
        "cq_repeat_after_failure_01",
        "cq_symptom_missing_triage_01",
        "cq_wellness_vague_01",
    ]
    assert all(cases[cid].user_input_needed for cid in ask_cases)
    # 움직이면 안 되는 둘 — 병명 확답 요구와 실제 응급은 지금 동작이 정답이다.
    redirect = sorted(cid for cid, case in cases.items() if case.expected_mode == "REDIRECT")
    assert redirect == ["cq_emergency_immediate_01", "cq_explicit_diagnosis_request_01"]


# ── 기록으로 먼저 답하고 되묻는다 (#415 2차) ──────────────────────────
#
# 스크린샷의 진짜 실패는 되묻지 않은 것만이 아니었다. 그 요청에는 **오늘의 케어 로그가
# 실려 있었는데**(`routers/assistant.py` 가 앱 회원 + 활성 강아지면 매번 얹는다) 프롬프트가
# "If the question is not about today's care, ignore the log" 라고 시켜서 버려졌다.
# `오늘 건강 상태는 어때?` 는 그 규칙이 말하는 "오늘 밥 줬나" 가 아니기 때문이다.
# 사람 결정: **기록은 사실대로 말하고, 판단은 안 붙이고, 못 채우는 칸만 되묻는다.**

SUMMARY = "오늘 밥 2번 먹었고(마지막 18:30) 산책도 1번 다녀왔네요. 투약 기록은 아직 없어요."


def test_an_ask_carries_the_question_apart_from_what_it_can_already_say() -> None:
    """되묻기는 이제 두 조각이다 — `text`(기록으로 말할 수 있는 것)와 `question`(물을 것).

    한 칸에 뭉쳐 담으면 `ClarifyRequest.question` 이 "질문" 이 아니라 문단이 되고,
    되묻기만 따로 렌더하려는 클라이언트가 질문을 다시 잘라내야 한다.
    """
    ok = validate_general_answer({"kind": "ask", "text": SUMMARY, "question": ASK, "reason": None})
    assert ok is not None and ok.question == ASK and ok.text == SUMMARY
    # 기록이 없으면 말할 것이 없다 — 질문만 나간다.
    assert validate_general_answer({"kind": "ask", "text": "", "question": ASK, "reason": None})
    # 질문 없는 되묻기는 되묻기가 아니다.
    assert validate_general_answer({"kind": "ask", "text": SUMMARY, "reason": None}) is None
    # 답과 거절은 질문 칸을 쓰지 않는다.
    assert validate_general_answer({"kind": "answer", "text": "답", "question": ASK}) is None
    assert (
        validate_general_answer(
            {"kind": "refuse", "text": "", "question": ASK, "reason": "diagnosis"}
        )
        is None
    )


async def test_adapter_keeps_the_grounded_part_next_to_the_question() -> None:
    result = await run_adapter(
        json.dumps({"kind": "ask", "text": f" {SUMMARY} ", "question": f" {ASK} ", "reason": None})
    )
    assert result.status == CapabilityStatus.OK
    assert result.data == {
        "answer": SUMMARY,
        "ask": {"question": ASK, "missing": [GENERAL_ASK_MISSING]},
    }


def test_the_clarify_message_leads_with_the_records_and_ends_with_the_question() -> None:
    """사용자가 보는 것은 `message` 한 칸이다 — 거기에 둘 다 있어야 한다.

    `clarify.question` 은 질문만 갖는다. 둘을 같은 값으로 두면 되묻기를 따로 렌더하는
    클라이언트가 기록 요약까지 질문 자리에 그린다.
    """
    result = CapabilityResult(
        capability=CapabilityName.GENERAL,
        status=CapabilityStatus.OK,
        data={"answer": SUMMARY, "ask": {"question": ASK, "missing": [GENERAL_ASK_MISSING]}},
        elapsed_ms=1,
    )
    response = aggregate_results(request_id="r1", route_plan=plan("general"), results=[result])
    assert response.status == AssistantStatus.CLARIFY
    assert response.message == f"{SUMMARY}\n\n{ASK}"
    assert response.clarify is not None and response.clarify.question == ASK


def test_without_records_the_message_is_just_the_question() -> None:
    """비로그인·활성 강아지 없음이면 얹을 기록이 없다 — 그때는 질문 하나로 돌아간다."""
    response = aggregate_results(
        request_id="r1", route_plan=plan("general"), results=[ask_result()]
    )
    assert response.message == ASK


def test_the_care_log_rule_no_longer_sends_todays_condition_question_away() -> None:
    """**이 한 줄이 스크린샷의 원인이었다.**

    `오늘 건강 상태는 어때?` 는 "오늘 밥 줬나" 가 아니라서 옛 규칙이 로그를 버렸다.
    그 요청에는 밥·투약·산책 건수와 마지막 시각이 실려 있었는데도 견종 하나만 남아
    백과사전 문장이 나갔다.
    """
    payload = GeneralPayload(question=QUERY, care_log=CARE_LOG)
    prompt = build_general_prompt(payload)
    assert "If the question is not about today's care, ignore the log." not in prompt
    assert "A question about how the dog is doing today" in prompt
    assert "Report what is recorded and what is not" in prompt
    # 사실 범위를 벗어나지 않게 — 답은 "기록상" 이라고 말해야 한다.
    assert "always framed as 기록상 / 기록에는" in prompt


def test_the_care_log_rule_forbids_calling_the_dog_fine_from_a_record() -> None:
    """사람이 그은 선: **사실만, 판단은 안 붙임.** 기록은 무슨 일이 있었는지를 말하지
    아이가 어떤지를 말하지 않는다."""
    prompt = build_general_prompt(GeneralPayload(question=QUERY, care_log=CARE_LOG))
    assert "never call the dog healthy, fine, normal, unwell, or lacking from it" in prompt
    # v3 부터 서 있던 선도 그대로다.
    assert "Never infer a dose, a schedule, or whether more is needed from it" in prompt


def test_the_base_prompt_admits_the_records_cannot_tell_instead_of_lecturing() -> None:
    """기록 규칙은 로그가 있을 때만 붙는다. 로그가 없어도 **되묻기 전에 그 사실을 말한다** —
    그냥 질문만 던지면 사용자는 앱이 자기 기록을 안 본 건지 없는 건지 알 수 없다."""
    prompt = build_general_prompt(GeneralPayload(question=QUERY))
    assert "when no rule below hands you any" in prompt
    assert "today's records alone cannot tell how the dog is" in prompt
    assert "CARE_LOG_TODAY" not in prompt


# ── 사람이 지정한 케이스 여덟 (2026-09-10) ────────────────────────────
#
# ⚠ **`evals/conversation_quality/cases_v1.jsonl` 에는 못 넣습니다.** 그 파일은 `#401` 의
# `cases_sha256` 로 핀 박혀 있어서, 한 줄만 더해도 before 랩과의 `compare` 가 거부됩니다
# (`report.render_compare`). 새 케이스는 케이스 파일의 **다음 판**에 들어갈 몫이고, 여기서는
# 계약·프롬프트 쪽 절반을 유닛으로 고정합니다. 모델이 실제로 그렇게 답하는지는 랩의 몫입니다.

CARE_LOG_CONTEXT = {
    "care_log": {
        "day": "2026-09-10",
        "meal": 2,
        "medication": 0,
        "snack": 0,
        "walk": 1,
        "last_meal_at": "18:30",
    }
}
GROUNDED_ASK = (
    "오늘 기록에는 식사 2회와 산책 1회가 있어요. 기록만으로 건강 상태를 판단할 수는 없는데, "
    "평소와 비교해 식욕·활력·배변이나 구토·호흡에서 달라진 점이 있나요? "
    "가장 눈에 띄는 것부터 말씀해 주세요."
)


async def test_case1_condition_question_with_todays_records() -> None:
    """① 오늘 기록이 있는 상태의 `오늘 건강 상태는 어때?` — 기록이 프롬프트에 실리고,
    되묻기가 그 기록을 앞세운 채 나간다."""
    transport = Transport(
        json.dumps(
            {
                "kind": "ask",
                "text": "오늘 기록에는 식사 2회와 산책 1회가 있어요.",
                "question": GROUNDED_ASK,
                "reason": None,
            }
        )
    )
    result = await GeneralCapabilityAdapter(generate=transport).run(
        general_request(CARE_LOG_CONTEXT), request_id="r1"
    )
    assert "CARE_LOG_TODAY" in transport.prompts[0]
    assert '"meal": 2' in transport.prompts[0] and '"walk": 1' in transport.prompts[0]
    response = aggregate_results(request_id="r1", route_plan=plan("general"), results=[result])
    assert response.status == AssistantStatus.CLARIFY
    assert response.message.startswith("오늘 기록에는 식사 2회와 산책 1회가 있어요.")
    assert response.message.endswith(GROUNDED_ASK)
    assert response.clarify is not None and response.clarify.question == GROUNDED_ASK


def test_case2_condition_question_without_any_records() -> None:
    """② 기록이 없는 상태의 같은 질문 — 로그 블록이 아예 없고, 기본 본문이 "오늘 기록만으로는
    알기 어렵다" 를 말하게 시킨다."""
    prompt = build_general_prompt(GeneralPayload(question=QUERY))
    assert "CARE_LOG_TODAY:" not in prompt
    assert "today's records alone cannot tell how the dog is" in prompt


def test_case3_a_reported_sign_is_not_closed_with_a_hospital_line() -> None:
    """③ `오늘 힘이 없어 보여` — 증상을 말한 것이지 진단을 요구한 것이 아니다.

    v3 는 증상이 언급되면 "병원 가 보세요" 한 줄로 **닫으라고**(`and nothing more`) 시켰다.
    그 줄이 스크린샷의 일반론·막다른 길을 만든 규칙 중 하나다.
    """
    prompt = build_general_prompt(GeneralPayload(question="오늘 힘이 없어 보여"))
    assert "and nothing more" not in prompt
    assert "A symptom the owner mentions is NOT a request for a diagnosis" in prompt
    assert "Do not close the conversation by sending them to a hospital" in prompt


def test_case4_a_complaint_about_this_conversation_is_not_off_topic() -> None:
    """④ `그니까 그걸 네가 물어봐야지` — 지금은 `off_topic` 거절이 나간다(스크린샷 3).

    무상태라 그 문장만 놓고 보면 강아지 얘기가 아니어서 나는 실패다. 앞 턴을 잇는 것은
    `#416` 이지만, **대화 자체에 대한 말을 주제 이탈로 읽지 않는 것**은 여기서 막을 수 있다.
    """
    prompt = build_general_prompt(GeneralPayload(question="그니까 그걸 네가 물어봐야지"))
    assert "A message about this conversation itself" in prompt
    assert "is NOT off_topic" in prompt


def test_case5_a_follow_up_answer_needs_416_but_is_not_a_diagnosis_request() -> None:
    """⑤ `밥은 잘 먹는데 계속 누워 있어` — 되묻기에 대한 **대답**이다.

    이것을 앞 질문과 잇는 것은 `#416` 이다(무상태인 지금은 새 질문으로 읽힌다). 이 카드가
    책임지는 것은 그 문장이 `diagnosis` 거절로 닫히지 않는 것까지다.
    """
    prompt = build_general_prompt(GeneralPayload(question="밥은 잘 먹는데 계속 누워 있어"))
    assert (
        "only an explicit request for a disease name, for the cause, or for a test reading"
        in prompt
    )


def test_case6_a_missing_entry_is_not_a_missed_meal() -> None:
    """⑥ 미기록을 "안 했다" 로 읽지 않는다.

    투약 기록이 0인 것은 약을 안 줬다는 뜻도, 줄 필요가 없다는 뜻도 아니다 — 기록에 없다는
    뜻뿐이다. 이걸 안 박아 두면 "오늘 투약을 안 하셨네요" 가 사실처럼 나간다.
    """
    prompt = build_general_prompt(GeneralPayload(question=QUERY, care_log=CARE_LOG))
    assert "A missing entry means the record has no entry" in prompt
    assert "It does NOT mean the dog did not eat, was not walked, or was not medicated" in prompt
    assert "say that the record has none, never that it did not happen" in prompt


def test_case7_non_urgent_signs_are_not_on_the_emergency_list() -> None:
    """⑦ 비응급 증상이 거절로 안 간다. `cq_symptom_missing_triage_01`(반복 구토)이 before
    랩에서 `emergency` 로 닫힌 자리다."""
    prompt = build_general_prompt(GeneralPayload(question="강아지가 오늘 세 번이나 토했어"))
    assert "Vomiting, diarrhea, limping, and appetite loss are not on this list" in prompt


async def test_case8_an_emergency_is_answered_at_once_not_asked_back() -> None:
    """⑧ 응급 신호에는 되묻기보다 즉시 안내가 먼저다 — 규칙으로도, 매핑으로도."""
    prompt = build_general_prompt(GeneralPayload(question="갑자기 경련을 일으켜"))
    assert "an emergency is answered at once, never asked back" in prompt
    result = await run_adapter(json.dumps({"kind": "refuse", "text": "", "reason": "emergency"}))
    response = aggregate_results(request_id="r1", route_plan=plan("general"), results=[result])
    assert response.status == AssistantStatus.REFUSED
    assert response.clarify is None
    assert "지금 바로 동물병원" in response.message
