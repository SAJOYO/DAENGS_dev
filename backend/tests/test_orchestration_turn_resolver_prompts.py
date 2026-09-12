"""프롬프트 렌더링 (#416 Task 6): 맥락이 없으면 오늘과 바이트 동일, 있으면 `USER_QUERY:`
바로 앞에 `CONVERSATION:` 한 블록.

`ConversationContext` 를 실제로 렌더하는 지점은 두 곳뿐이다 —
`semantic.build_semantic_router_prompt` 와 `general.build_general_prompt`. 두 빌더 모두
"맥락이 없을 때"의 몸이 오늘과 한 글자도 다르지 않아야 한다는 것이 이 파일의 핵심
불변식이다: 라우터의 `PROMPT_VERSION` 은 라우팅 벤치마크의 핀이고, General 의 `v6` 계열은
84건 쌍대 비교로 승인된 본문이다.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from daengs_backend.orchestration.adapters.general import (
    _CARE_LOG_RULE,
    _SAFETY_PROMPT,
    _UNMEASURED_RULE,
    _VET_SPEND_RULE,
    GENERAL_CARE_LOG_PROMPT_VERSION,
    GENERAL_CARE_LOG_VET_PROMPT_VERSION,
    GENERAL_PROMPT_VERSION,
    GENERAL_VET_PROMPT_VERSION,
    GeneralAnswer,
    build_general_prompt,
    general_prompt_version,
)
from daengs_backend.orchestration.contracts import (
    CareLogContext,
    GeneralPayload,
    LastVetVisitContext,
    ObservationAxis,
    VetSpendContext,
)
from daengs_backend.orchestration.resolver import ConversationContext, TurnRelation
from daengs_backend.orchestration.semantic import (
    _POLICY,
    PROMPT_VERSION,
    RESOLVED_PROMPT_VERSION,
    SemanticRoutingDecision,
    build_semantic_router_prompt,
    render_conversation_context,
    routing_metadata,
)

QUERY = "그거 얼마나 자주 해?"

CARE_LOG = CareLogContext(day=date(2026, 9, 8), meal=2, medication=1, snack=0, walk=1)
VET_SPEND = VetSpendContext(
    month_total_krw=80_000,
    visit_count_30d=1,
    last_visit=LastVetVisitContext(date=date(2026, 9, 2), reason="피부", total_krw=80_000),
)


def _follow_up(**overrides: object) -> ConversationContext:
    base: dict[str, object] = {
        "relation": TurnRelation.FOLLOW_UP,
        "referenced_original_request": "사료 추천해줘",
    }
    base.update(overrides)
    return ConversationContext(**base)


# ---------------------------------------------------------------- semantic router


def _reconstructed_router_prompt(*, query: str, context: dict[str, object]) -> str:
    """`build_semantic_router_prompt` 의 no-context 조립을 이 테스트가 독립적으로 다시
    짠 것 — 구현을 그대로 불러 비교하면 순서·구분자 회귀를 못 잡는다."""
    metadata = routing_metadata(context)
    schema = json.dumps(
        SemanticRoutingDecision.model_json_schema(), ensure_ascii=False, sort_keys=True
    )
    return (
        f"PROMPT_VERSION: {PROMPT_VERSION}\n\n"
        f"{_POLICY}\n\n"
        f"SEMANTIC_DECISION_JSON_SCHEMA:\n{schema}\n\n"
        f"INPUT_LOCALE: ko-KR\n"
        f"ROUTING_METADATA: {json.dumps(metadata, ensure_ascii=False, sort_keys=True)}\n"
        f"USER_QUERY: {query}\n"
    )


def test_router_prompt_is_byte_identical_without_a_resolution() -> None:
    """수용 케이스 1 — 이력이 안 닿는 요청은 오늘과 **같은 문자열**이다."""
    context = {"source": "app", "active_dog_id": "d1"}
    without_resolved = build_semantic_router_prompt(query="사료 추천해줘", context=context)
    explicit_none = build_semantic_router_prompt(
        query="사료 추천해줘", context=context, resolved=None
    )
    assert without_resolved == explicit_none
    assert without_resolved == _reconstructed_router_prompt(
        query="사료 추천해줘", context=context
    )
    assert f"PROMPT_VERSION: {PROMPT_VERSION}\n\n" in without_resolved
    assert "CONVERSATION:" not in without_resolved
    assert without_resolved.rstrip().endswith("USER_QUERY: 사료 추천해줘")


def test_resolution_goes_before_the_user_query_and_flips_the_version() -> None:
    context = {"source": "app"}
    resolved_ctx = _follow_up()
    prompt = build_semantic_router_prompt(
        query=QUERY, context=context, resolved=resolved_ctx
    )
    assert prompt.index("CONVERSATION:") < prompt.index("USER_QUERY:")
    assert prompt.rstrip().endswith(f"USER_QUERY: {QUERY}")
    assert f"PROMPT_VERSION: {RESOLVED_PROMPT_VERSION}\n\n" in prompt
    assert f"PROMPT_VERSION: {PROMPT_VERSION}\n\n" not in prompt
    # PROMPT_VERSION 자체가 아니라 프롬프트 몸이 오늘과 다르다는 것도 확인한다 — 정책·
    # 스키마·메타데이터 줄은 그대로이고 CONVERSATION 블록만 새로 끼어든다.
    baseline = build_semantic_router_prompt(query=QUERY, context=context)
    inserted = prompt.replace(f"{render_conversation_context(resolved_ctx)}\n", "", 1)
    assert inserted == baseline.replace(PROMPT_VERSION, RESOLVED_PROMPT_VERSION, 1)


def test_resolved_none_and_omitted_are_the_same_call() -> None:
    """`select()` 가 `resolved=None` 을 기본값으로 넘기는 경로와 완전히 생략한 호출이
    같은 프롬프트를 만든다 — 시그니처가 넓어져도 기존 호출부가 안전하다는 뜻이다."""
    context = {"source": "app"}
    assert build_semantic_router_prompt(query=QUERY, context=context) == (
        build_semantic_router_prompt(query=QUERY, context=context, resolved=None)
    )


# ---------------------------------------------------------------- general fallback


def _schema() -> str:
    return json.dumps(GeneralAnswer.model_json_schema(), ensure_ascii=False, sort_keys=True)


def _independently_reconstructed_prompt(
    *,
    version: str,
    dog: dict[str, object],
    care_log: dict[str, object] | None,
    vet_spend: dict[str, object] | None,
) -> str:
    """오늘(D-072 Task 8 이후) `build_general_prompt` 의 조립 규칙을 **이 테스트가 직접**
    다시 짠 것 — 구현을 그대로 불러 비교하면 구현이 통째로 틀려도 자기 자신과는 늘 같다.
    안전 프롬프트·규칙 문단 상수는 이 카드가 건드리지 않는 프로즈라 그대로 가져다 쓰지만,
    줄 순서·구분자·개행은 여기서 독립적으로 다시 쓴다. `_UNMEASURED_RULE` 은 Task 8 부터
    `care_log`/`vet_spend` 유무와 무관하게 항상 붙는다 — 그래서 더 이상 "둘 다 없으면
    이 문단들 자체가 없다"는 특수 분기가 없다."""
    rule_blocks = [_SAFETY_PROMPT, _UNMEASURED_RULE]
    if care_log is not None:
        rule_blocks.append(_CARE_LOG_RULE)
    if vet_spend is not None:
        rule_blocks.append(_VET_SPEND_RULE)
    context_lines = [f"DOG_CONTEXT: {json.dumps(dog, ensure_ascii=False, sort_keys=True)}"]
    if care_log is not None:
        context_lines.append(f"CARE_LOG_TODAY: {json.dumps(care_log, ensure_ascii=False, sort_keys=True)}")
    if vet_spend is not None:
        context_lines.append(f"VET_RECENT: {json.dumps(vet_spend, ensure_ascii=False, sort_keys=True)}")
    return (
        f"PROMPT_VERSION: {version}\n\n"
        + "\n\n".join(rule_blocks)
        + "\n\n"
        + f"GENERAL_ANSWER_JSON_SCHEMA:\n{_schema()}\n\n"
        + "\n".join(context_lines)
        + "\n"
        + f"USER_QUERY: {QUERY}\n"
    )


def test_general_prompt_is_byte_identical_without_a_conversation_for_all_four_combinations() -> (
    None
):
    """수용 케이스 2 — care_log × vet_spend 의 네 조합 모두, `conversation` 이 없으면
    오늘 그대로다. 하나만 확인하면 다른 셋의 리터럴 분기가 조용히 깨져도 못 잡는다.

    비교 대상은 이 테스트가 **독립적으로 재조립한** 문자열이다 — 구현을 그대로 불러
    비교하면 구현이 통째로 틀려도 자기 자신과는 늘 같아서, 순서나 구분자가 어긋나는
    회귀를 못 잡는다."""
    combinations = [
        (GeneralPayload(question=QUERY), {}, None, None, GENERAL_PROMPT_VERSION),
        (
            GeneralPayload(question=QUERY, care_log=CARE_LOG),
            {},
            CARE_LOG.model_dump(mode="json", exclude_none=True),
            None,
            GENERAL_CARE_LOG_PROMPT_VERSION,
        ),
        (
            GeneralPayload(question=QUERY, vet_spend=VET_SPEND),
            {},
            None,
            VET_SPEND.model_dump(mode="json", exclude_none=True),
            GENERAL_VET_PROMPT_VERSION,
        ),
        (
            GeneralPayload(question=QUERY, care_log=CARE_LOG, vet_spend=VET_SPEND),
            {},
            CARE_LOG.model_dump(mode="json", exclude_none=True),
            VET_SPEND.model_dump(mode="json", exclude_none=True),
            GENERAL_CARE_LOG_VET_PROMPT_VERSION,
        ),
    ]
    for payload, dog, care_log, vet_spend, expected_version in combinations:
        assert general_prompt_version(payload) == expected_version
        expected = _independently_reconstructed_prompt(
            version=expected_version, dog=dog, care_log=care_log, vet_spend=vet_spend
        )
        assert build_general_prompt(payload) == expected
        assert "CONVERSATION:" not in build_general_prompt(payload)


def test_general_version_gets_a_conv_suffix_when_conversation_rides_along() -> None:
    payload = GeneralPayload(question=QUERY, conversation=_follow_up())
    assert general_prompt_version(payload) == f"{GENERAL_PROMPT_VERSION}-conv"
    prompt = build_general_prompt(payload)
    assert "CONVERSATION:" in prompt
    assert f"PROMPT_VERSION: {GENERAL_PROMPT_VERSION}-conv\n\n" in prompt
    assert prompt.index("CONVERSATION:") < prompt.index("USER_QUERY:")
    assert prompt.rstrip().endswith(f"USER_QUERY: {QUERY}")


@pytest.mark.parametrize(
    ("care_log", "vet_spend", "base_version"),
    [
        (CARE_LOG, None, GENERAL_CARE_LOG_PROMPT_VERSION),
        (None, VET_SPEND, GENERAL_VET_PROMPT_VERSION),
        (CARE_LOG, VET_SPEND, GENERAL_CARE_LOG_VET_PROMPT_VERSION),
    ],
)
def test_conv_suffix_applies_on_top_of_every_base_version(
    care_log: CareLogContext | None, vet_spend: VetSpendContext | None, base_version: str
) -> None:
    """`-conv` 는 접미사다 — care_log/vet_spend 조합 중 어느 것 위에도 붙는다."""
    payload = GeneralPayload(
        question=QUERY, care_log=care_log, vet_spend=vet_spend, conversation=_follow_up()
    )
    assert general_prompt_version(payload) == f"{base_version}-conv"
    prompt = build_general_prompt(payload)
    assert prompt.index("CONVERSATION:") < prompt.index("USER_QUERY:")
    assert prompt.rstrip().endswith(f"USER_QUERY: {QUERY}")


@pytest.mark.parametrize(
    ("care_log", "vet_spend"),
    [
        (None, None),
        (CARE_LOG, None),
        (None, VET_SPEND),
        (CARE_LOG, VET_SPEND),
    ],
)
def test_general_conversation_body_is_the_base_body_with_one_block_inserted(
    care_log: CareLogContext | None, vet_spend: VetSpendContext | None
) -> None:
    """R19 — `general.py` 는 (fix round 1 전) 승인된 기본 본문을 `conversation is None`
    반환문과 `-conv` 반환문에 **따로** 적는다. 그 둘이 몸에서 갈라지면 한쪽만 고치고
    다른 쪽을 잊어도 아무 것도 안 걸린다 — `semantic.py` 의
    `test_resolution_goes_before_the_user_query_and_flips_the_version` 이 이미 쓰는
    "base 를 버전만 바꾸고 블록 하나만 끼운 것과 같은가" 패턴을 General 의 네 조합
    전부에 적용한다."""
    context = _follow_up()
    base_payload = GeneralPayload(question=QUERY, care_log=care_log, vet_spend=vet_spend)
    with_conv_payload = GeneralPayload(
        question=QUERY, care_log=care_log, vet_spend=vet_spend, conversation=context
    )
    base_prompt = build_general_prompt(base_payload)
    with_conv_prompt = build_general_prompt(with_conv_payload)
    base_version = general_prompt_version(base_payload)
    conv_version = general_prompt_version(with_conv_payload)
    assert conv_version == f"{base_version}-conv"

    inserted = with_conv_prompt.replace(f"{render_conversation_context(context)}\n", "", 1)
    assert inserted == base_prompt.replace(
        f"PROMPT_VERSION: {base_version}", f"PROMPT_VERSION: {conv_version}", 1
    )


# ---------------------------------------------------------------- render_conversation_context


def test_pending_axes_are_labelled_as_asked_not_observed() -> None:
    """건강 어시스턴트에서 실제 사고로 이어지는 오독 — `APPETITE` 가 "물어본 항목"이지
    "식욕에 문제가 있다"는 관찰이 아니라는 것이 키 이름 자체에서 읽혀야 한다."""
    resolved = _follow_up(
        pending_question="식욕은 어땠나요?", pending_missing_axes=[ObservationAxis.APPETITE]
    )
    block = render_conversation_context(resolved)
    assert "APPETITE" in block
    # R20 — 여기 있던 `assert "asked" in block` 은 지웠다: `pending_question_previously_
    # asked_by_the_assistant` 키가 `pending_question is None` 이어도 무조건 나가므로
    # "asked" 는 항상 참이라 절대 실패할 수 없었다(#416 Task 6 리뷰). 아래 줄만 실패
    # 가능한 진짜 확인이다 — 축이 그 정확한(오독 방지) 키 아래에 실려 있는지.
    assert '"pending_axes_the_assistant_asked_about_not_dog_observations"' in block


def test_standalone_query_is_labelled_as_a_model_restatement() -> None:
    """`standalone_query` 는 사용자의 말이 아니다 — 그 구분이 키 이름에 있어야, 이 블록만
    보고 "사용자가 이렇게 말했다"로 착각하지 않는다."""
    resolved = _follow_up(standalone_query="그 사료는 얼마나 자주 급여해야 하나요?")
    block = render_conversation_context(resolved)
    assert "그 사료는 얼마나 자주 급여해야 하나요?" in block
    assert '"standalone_query_is_a_model_restatement_not_the_users_words"' in block


def test_render_includes_a_short_usage_instruction() -> None:
    """참조를 풀고 반복/정정을 알아채되, 새 주제면 무시하라는 지시문이 실제로 있다."""
    block = render_conversation_context(_follow_up())
    assert block.startswith("CONVERSATION_INSTRUCTION:")
    assert "new topic" in block
    assert "ignore" in block


def test_render_reflects_all_five_fields() -> None:
    resolved = ConversationContext(
        relation=TurnRelation.CORRECTION,
        referenced_original_request="산책 시간표 알려줘",
        standalone_query="산책 말고 사료 급여량 알려줘",
        pending_question="식욕은 어땠나요?",
        pending_missing_axes=[ObservationAxis.APPETITE, ObservationAxis.ENERGY],
    )
    block = render_conversation_context(resolved)
    assert "CORRECTION" in block
    assert "산책 시간표 알려줘" in block
    assert "산책 말고 사료 급여량 알려줘" in block
    assert "식욕은 어땠나요?" in block
    assert "APPETITE" in block and "ENERGY" in block


def test_referenced_assistant_answer_is_rendered_under_a_key_distinct_from_the_users_words() -> (
    None
):
    """followup-answer-text — 참조한 턴에서 **비서가 답한 내용**이 실제로 렌더된다. 키
    이름이 `referenced_original_request`(사용자 발화)와 헷갈리면 실제 피해로 이어진다는
    것이 브리프의 요점이므로, 값뿐 아니라 그 값이 실린 키 이름까지 확인한다.
    `render_conversation_context` 가 `referenced_assistant_answer` 를 payload 조립에서
    빠뜨리면(혹은 `referenced_original_request` 키 아래에 잘못 실으면) 이 테스트가 실패한다.
    """
    resolved = _follow_up(
        referenced_assistant_answer="소형견 저알레르기 사료를 하루 두 번 급여하세요.",
    )
    block = render_conversation_context(resolved)
    assert '"referenced_turn_the_assistant_actually_answered"' in block
    assert "소형견 저알레르기 사료를 하루 두 번 급여하세요." in block
    payload = json.loads(block.split("CONVERSATION: ", 1)[1])
    assert (
        payload["referenced_turn_the_assistant_actually_answered"]
        == "소형견 저알레르기 사료를 하루 두 번 급여하세요."
    )
    # 사용자 발화 칸과 값이 섞이지 않는다.
    assert payload["referenced_original_request"] == "사료 추천해줘"


def test_referenced_assistant_answer_absent_renders_as_null_not_dropped() -> None:
    """참조가 없는 새 주제 turn 은 이 칸이 `null` 로 나가야 한다 — 키 자체가 사라지면
    General 프롬프트의 "맥락이 없으면 바이트 동일" 불변식과는 별개로, 이 칸을 읽는 규칙이
    깨진 JSON 구조를 만나게 된다. `render_conversation_context` 의 payload 딕셔너리에서
    이 키를 빼면 이 테스트가 실패한다."""
    resolved = ConversationContext(relation=TurnRelation.NEW)
    block = render_conversation_context(resolved)
    payload = json.loads(block.split("CONVERSATION: ", 1)[1])
    assert "referenced_turn_the_assistant_actually_answered" in payload
    assert payload["referenced_turn_the_assistant_actually_answered"] is None
