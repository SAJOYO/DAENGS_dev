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
    GeneralPayload,
    RoutePlan,
    RouterKind,
)
from daengs_backend.orchestration.planner import _payload_for
from daengs_evals.conversation_quality.cases import load_cases

pytestmark = pytest.mark.anyio

QUERY = "오늘 건강 상태는 어때?"
ASK = "오늘 평소와 달라 보이는 점이 있나요?"


# ── 모델 출력 스키마: 세 번째 kind ────────────────────────────────────


def test_answer_schema_has_a_third_kind_for_asking_back() -> None:
    """`ask` 가 스키마에 없으면 제약 디코딩이 그 출력을 아예 못 낸다.

    `kind` 는 `Literal` 이라 JSON 스키마의 enum 으로 나간다 — 값을 안 더하면 모델은
    되묻고 싶어도 `answer` 나 `refuse` 중 하나로 눌러 담는다. before 랩이 본 것이 그것이다.
    """
    kinds = GeneralAnswer.model_json_schema()["properties"]["kind"]["enum"]
    assert sorted(kinds) == ["answer", "ask", "refuse"]


def test_an_ask_carries_text_and_no_refusal_reason() -> None:
    """`ask` 는 답이 아니라 질문이다 — 문장은 필수, 거절 사유는 없어야 한다."""
    assert validate_general_answer({"kind": "ask", "text": ASK, "reason": None}) is not None
    assert validate_general_answer({"kind": "ask", "text": "", "reason": None}) is None
    assert validate_general_answer({"kind": "ask", "text": ASK, "reason": "diagnosis"}) is None


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
    result = await run_adapter(json.dumps({"kind": "ask", "text": f"  {ASK} ", "reason": None}))
    assert result.status == CapabilityStatus.OK
    assert result.data == {"ask": {"question": ASK, "missing": [GENERAL_ASK_MISSING]}}
    assert result.capability == CapabilityName.GENERAL and result.elapsed_ms >= 0


async def test_adapter_rejects_an_ask_that_the_clarify_contract_cannot_hold() -> None:
    """`ClarifyRequest.question` 은 500자다. 어댑터가 그 자리에서 걸러야 집계가 안 터진다."""
    result = await run_adapter(json.dumps({"kind": "ask", "text": "가" * 501, "reason": None}))
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


def test_prompt_asks_only_for_what_the_owner_can_see() -> None:
    """승인된 경계 (D-068): 관찰 축만 묻고, 구체 증상이 오면 병원으로.

    문진표를 걷거나 병명 후보를 늘어놓는 것은 증상을 근거로 판단하는 흐름이라 지금 의료
    경계 밖이다. 질문은 **하나**다.
    """
    prompt = build_general_prompt(GeneralPayload(question=QUERY))
    assert "ask: " in prompt
    assert "ONE short Korean question" in prompt
    assert "only for what the owner can see" in prompt
    assert "Never list candidate diseases" in prompt
    assert "never walk a diagnostic checklist" in prompt
    assert "never ask more than one question" in prompt


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
