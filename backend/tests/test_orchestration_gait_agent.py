"""보행 **변화 관찰** 해설 서브에이전트 (D-080).

피부 해설(D-079)과 배선이 같아 보이지만 **목적이 달라서** 지켜야 할 것이 다릅니다. 이
파일이 지키는 것은 그 차이입니다:

1. **진입** — 명시 신호 `gait` 에 **서버가 소유를 확인하고 계산한 비교**가 붙었을 때만.
   라우터는 이 능력을 못 고르고, 참조가 없으면 예전 HANDOFF 입니다.
2. **좁힘** — payload 에 관절 수치 · 관절 이름 · 방향의 칸이 **없습니다**.
3. **갈래 파생이 앱과 같습니다** — `change_kind` 는 앱 `verdictOf` 와 같은 규칙이어야
   합니다. 특히 `not_enough` 가 무너지면 못 잰 것이 "뚜렷한 차이 없음" 으로 흘러들어
   **없는 안심**을 줍니다 (7단계가 서버를 정본으로 만든 이유).
4. **안전 규칙은 코드가 지킵니다** — 행동 순서(`plan_gait_actions`)와 해설 문장
   (`speaks_beyond_change`). 특히 **방향어 금지**가 이 능력의 핵심입니다: 비교 계산 자체가
   방향을 말하지 않는데(`compare.direction_note`) 해설이 말하면 계산이 하지 않은 판단을
   문장이 해 버립니다.
5. **비교 실패도 답합니다** — 조용한 HANDOFF 가 아니라 이유 범주별 고정 문구로 닫고,
   그 경로에서는 **모델을 아예 안 태웁니다**.
"""

from __future__ import annotations

import datetime
import json
import uuid
from typing import Any

import pytest

from daengs_backend.orchestration.adapters.gait import (
    GAIT_PROMPT_VERSION,
    GaitCapabilityAdapter,
    build_gait_prompt,
    plan_gait_actions,
    speaks_beyond_change,
    validate_gait_guidance,
)
from daengs_backend.orchestration.contracts import (
    CapabilityName,
    CapabilityRequest,
    CapabilityStatus,
    GaitCompareContext,
    GaitComparePayload,
    RouterKind,
)
from daengs_backend.orchestration.planner import assemble_route_plan, resolve_gait_route
from daengs_backend.orchestration.redirects import (
    GAIT_ACTION_MESSAGES,
    GAIT_CHANGE_SUMMARY,
    GAIT_EXPERT_ADVISORY,
    GAIT_REFERENCE_NOTICE,
    GAIT_UNAVAILABLE_MESSAGES,
    GAIT_VERSION_WARNING,
    SCOPED_REDIRECT_MESSAGES,
)
from daengs_backend.orchestration.semantic import SemanticRoutingDecision
from daengs_backend.services import gait_context

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _compare(**over: Any) -> GaitCompareContext:
    base: dict[str, Any] = {
        "change_kind": "one_side",
        "flagged_sides": ["left"],
        "left_measured": 3,
        "left_joints": 3,
        "right_measured": 3,
        "right_joints": 3,
        "days_between": 14,
        "reliability": "ok",
        "version_mismatch": False,
        "expert_advisory": False,
    }
    base.update(over)
    return GaitCompareContext(**base)


def _request(payload: dict[str, Any]) -> CapabilityRequest:
    return CapabilityRequest.model_validate({"capability": "gait", "payload": payload})


def _guide(text: str, actions: list[str]) -> str:
    return json.dumps({"kind": "guide", "text": text, "actions": actions, "reason": None})


async def _run(adapter: GaitCapabilityAdapter, payload: dict[str, Any]):
    return await adapter.run(_request(payload), request_id="rid")


# ── 1. 진입 ──────────────────────────────────────────────────────────────────
def test_signal_without_a_comparison_reference_stays_a_handoff() -> None:
    """참조가 없으면 계획을 안 연다 — 같은 신호가 뒤에서 예전처럼 gait HANDOFF 가 된다."""
    assert (
        resolve_gait_route(
            query="지난번이랑 뭐가 달라?",
            context={},
            requested_capability="gait",
            enabled=True,
        )
        is None
    )


def test_kill_switch_off_returns_to_the_old_handoff() -> None:
    assert (
        resolve_gait_route(
            query="뭐가 달라?",
            context={"gait_compare": _compare().model_dump()},
            requested_capability="gait",
            enabled=False,
        )
        is None
    )


@pytest.mark.parametrize("signal", [None, "skin", "life", "walk", ""])
def test_only_the_gait_signal_opens_the_capability(signal: str | None) -> None:
    """라우터는 이 능력을 못 고른다 — 여는 것은 명시 신호 하나뿐이다."""
    assert (
        resolve_gait_route(
            query="뭐가 달라?",
            context={"gait_compare": _compare().model_dump()},
            requested_capability=signal,
            enabled=True,
        )
        is None
    )


def test_the_plan_is_exclusive_and_deterministic() -> None:
    plan = resolve_gait_route(
        query="왼쪽이 왜 변했다는 거야?",
        context={"gait_compare": _compare().model_dump()},
        requested_capability="gait",
        enabled=True,
    )
    assert plan is not None
    assert [r.capability for r in plan.requests] == [CapabilityName.GAIT]
    assert plan.handoffs == [] and plan.clarify is None
    assert plan.model is None and plan.prompt_version is None


def test_a_reason_alone_still_opens_the_capability() -> None:
    """비교를 못 했어도 계획이 열린다 — 사용자가 비교 화면에서 눌러 들어온 요청이라서다.

    피부(D-079)와 갈리는 유일한 지점이고, D-080 이 그렇게 정했다.
    """
    plan = resolve_gait_route(
        query="왜 비교가 안 돼?",
        context={"gait_compare_unavailable": {"reason": "different_pet"}},
        requested_capability="gait",
        enabled=True,
    )
    assert plan is not None
    payload = plan.requests[0].payload
    assert isinstance(payload, GaitComparePayload)
    assert payload.unavailable == "different_pet" and payload.compare is None


def test_an_unknown_reason_is_not_carried() -> None:
    """계약이 아는 범주만 통과한다 — 모르는 이름은 없는 것과 같다."""
    assert (
        resolve_gait_route(
            query="왜?",
            context={"gait_compare_unavailable": {"reason": "hmm"}},
            requested_capability="gait",
            enabled=True,
        )
        is None
    )


def test_a_question_longer_than_the_limit_falls_back_to_handoff() -> None:
    assert (
        resolve_gait_route(
            query="가" * 1_001,
            context={"gait_compare": _compare().model_dump()},
            requested_capability="gait",
            enabled=True,
        )
        is None
    )


# ── 2. payload 좁힘 ──────────────────────────────────────────────────────────
# ── 1-B. 진입: 라우터가 낸 HANDOFF 를 해설로 바꾸기 (D-081) ──────────────


def _routed(
    handoffs: list[str],
    *,
    execute: list[str] | None = None,
    context: dict[str, Any] | None = None,
    gait_agent: bool = True,
    skin_agent: bool = False,
):
    """라우터가 그 HANDOFF 를 냈을 때 조립되는 계획. **모델 호출 0.**"""
    return assemble_route_plan(
        SemanticRoutingDecision(execute=execute or [], handoffs=handoffs),
        query="지난번이랑 뭐가 달라?",
        context={"gait_compare": _compare().model_dump()} if context is None else context,
        router=RouterKind.LLM,
        gait_agent=gait_agent,
        skin_agent=skin_agent,
    )


def test_router_gait_handoff_becomes_the_explainer_when_a_comparison_is_attached() -> None:
    """사용자는 방금 비교를 봤고 이어서 물었다. 등록 카드를 다시 띄우는 것은 답이 아니다."""
    plan = _routed(["gait"])
    [only] = plan.requests
    assert only.capability == CapabilityName.GAIT
    assert only.payload.compare is not None
    assert only.payload.question == "지난번이랑 뭐가 달라?"
    assert plan.handoffs == [] and plan.clarify is None


def test_the_converted_plan_is_the_same_one_the_explicit_signal_builds() -> None:
    """진입이 둘이어도 계획은 한 곳에서 만들어져야 두 길이 다른 답을 내지 않는다."""
    by_signal = resolve_gait_route(
        query="지난번이랑 뭐가 달라?",
        context={"gait_compare": _compare().model_dump()},
        requested_capability="gait",
        enabled=True,
    )
    assert by_signal is not None
    assert _routed(["gait"]).requests == by_signal.requests


def test_a_comparison_that_could_not_be_resolved_returns_to_the_ordinary_planner() -> None:
    """⚠️ **이 PR 의 핵심 경계다** (D-081).

    `resolve_gait_route` 는 칩 경로에서 **비교를 못 했을 때도** 계획을 만든다 — 사용자가 비교
    화면에서 눌러 들어왔으니 이유를 말하고 닫는 것이 맞다(D-080). 그런데 타이핑 경로에서 낡은
    참조가 실리면 **그 대화의 모든 질문이 고정 실패 문구로 닫힌다.** 라우터가 gait 로 보낸
    질문은 전부 이 길을 지나기 때문이다.

    그래서 전환 경로에서는 비교 해소가 성공했을 때만 연다. 못 했으면 **오늘과 같은 HANDOFF**.
    """
    plan = _routed(["gait"], context={"gait_compare_unavailable": {"reason": "not_found"}})
    assert plan.requests == []
    assert [h.target for h in plan.handoffs] == ["gait"]


def test_no_gait_context_at_all_also_stays_a_handoff() -> None:
    plan = _routed(["gait"], context={})
    assert plan.requests == []
    assert [h.target for h in plan.handoffs] == ["gait"]


def test_the_kill_switch_off_returns_the_whole_entry_to_the_old_handoff() -> None:
    """⚠️ **회귀 스위치다.** 끄면 오늘과 한 글자도 다르지 않아야 한다."""
    plan = _routed(["gait"], gait_agent=False)
    assert plan.requests == []
    assert [h.target for h in plan.handoffs] == ["gait"]


def test_the_two_kill_switches_are_independent() -> None:
    """하나의 불리언으로 묶으면 한쪽을 끄려다 다른 쪽까지 꺼진다."""
    plan = _routed(["gait"], gait_agent=True, skin_agent=False)
    assert [r.capability for r in plan.requests] == [CapabilityName.GAIT]


def test_conversion_is_exclusive_and_drops_other_selections() -> None:
    """비교 이야기에 산책 조건이 섞이면 이어 물은 답이 흐려진다 — 명시 신호 때와 같은 규칙."""
    context = {
        "gait_compare": _compare().model_dump(),
        "location": {"lat": 37.5, "lon": 127.0},
    }
    plan = _routed(["gait"], execute=["walk", "life"], context=context)
    assert [r.capability for r in plan.requests] == [CapabilityName.GAIT]
    assert plan.handoffs == []


def test_a_handoff_the_router_did_not_choose_is_not_opened() -> None:
    """라우터의 판단은 바꾸지 않는다 — 목적지만 바꾼다. 산책 질문은 그대로 산책이 답한다."""
    plan = _routed([], execute=["walk"], context={"gait_compare": _compare().model_dump()})
    assert CapabilityName.GAIT not in [r.capability for r in plan.requests]


def test_the_payload_has_no_room_for_joint_numbers_or_names() -> None:
    """수치 · 관절 이름 · 방향은 **칸 자체가 없다** (불변식 15 의 형제).

    프롬프트 금지보다 앞선 방어다 — 모르는 것은 말할 수 없다.
    """
    fields = set(GaitCompareContext.model_fields)
    assert not fields & {
        "joints",
        "joint_names",
        "summary_for_ui",
        "x_range",
        "y_range",
        "direction",
        "raw_cosine",
    }
    with pytest.raises(ValueError):
        GaitCompareContext(**{**_compare().model_dump(), "x_range": 1.0})


def test_the_planner_copies_only_whitelisted_keys() -> None:
    """context 에 뭐가 더 붙어 있어도 payload 로는 계약의 칸만 간다."""
    plan = resolve_gait_route(
        query="뭐가 달라?",
        context={
            "gait_compare": {**_compare().model_dump(), "summary_for_ui": {"L_Hip": 1.0}},
        },
        requested_capability="gait",
        enabled=True,
    )
    assert plan is not None
    payload = plan.requests[0].payload
    assert isinstance(payload, GaitComparePayload)
    assert payload.compare is not None
    assert "summary_for_ui" not in payload.compare.model_dump()


def test_a_context_missing_a_contract_key_is_dropped_whole() -> None:
    plan = resolve_gait_route(
        query="뭐가 달라?",
        context={"gait_compare": {"change_kind": "one_side"}},
        requested_capability="gait",
        enabled=True,
    )
    assert plan is None


def test_the_payload_carries_either_a_comparison_or_a_reason_never_both() -> None:
    with pytest.raises(ValueError):
        GaitComparePayload(question="x")
    with pytest.raises(ValueError):
        GaitComparePayload(question="x", compare=_compare(), unavailable="not_found")


# ── 3. change_kind 파생 = 앱 verdictOf 와 같은 규칙 ──────────────────────────
#: 앱 `verdictOf`(`GaitJoints.kt`)의 판정 우선순위를 그대로 옮긴 표.
#:
#:   BothSides > OneSide > (NoClearDifference | NotEnough)
#:
#: ⚠️ 이 표를 고치면 앱도 같이 고쳐야 한다. 한쪽만 바꾸면 카드(앱)와 해설(서버)이 같은
#:    비교를 두고 다른 말을 한다 — 7단계가 없앤 바로 그 상태로 돌아간다.
_VERDICT_TABLE = [
    # (flagged, left(잰 수, 대상), right, 기대 갈래)
    (["left", "right"], (3, 3), (3, 3), "both_sides"),
    (["left"], (3, 3), (3, 3), "one_side"),
    (["right"], (3, 3), (3, 3), "one_side"),
    ([], (3, 3), (3, 3), "no_change"),
    ([], (2, 3), (2, 3), "no_change"),
    # 한쪽이라도 2개를 못 쟀으면 "비슷하다" 고 말할 근거가 없다.
    ([], (3, 3), (1, 3), "not_enough"),
    ([], (1, 3), (3, 3), "not_enough"),
    ([], (0, 0), (0, 0), "not_enough"),
    # 변화가 이미 기준을 채웠으면 못 잰 관절이 있어도 그대로 말한다.
    (["left"], (2, 3), (1, 3), "one_side"),
]


@pytest.mark.parametrize(("flagged", "left", "right", "expected"), _VERDICT_TABLE)
def test_change_kind_matches_the_app_verdict_rule(
    flagged: list[str], left: tuple[int, int], right: tuple[int, int], expected: str
) -> None:
    assert gait_context._change_kind(flagged, left, right) == expected


def test_measured_comes_from_joints_minus_unmeasured() -> None:
    """앱은 고정 6관절을 그리고 서버는 합집합만 센다 — 그래서 `n_joints` 가 아니라 **잰 수**다."""
    assert gait_context._counts({"n_joints": 3, "n_unmeasured": 1}) == (2, 3)
    assert gait_context._counts({"n_joints": 3, "n_unmeasured": 3}) == (0, 3)
    assert gait_context._counts(None) == (0, 0)


def test_legacy_shaped_side_summary_is_not_forced_into_legs() -> None:
    """좌/우로 안 갈리는 `전체` 버킷과 `side_summary` 부재는 다리별 판정에 못 쓴다."""
    assert gait_context._sides_of({"side_summary": {"전체": {"n_joints": 2}}}) is None
    assert gait_context._sides_of({}) is None
    assert gait_context._sides_of({"side_summary": {}}) is None
    assert set(gait_context._sides_of({"side_summary": {"왼쪽": {}, "오른쪽": {}}})) == {
        "left",
        "right",
    }


# ── 4. 행동 가드 ─────────────────────────────────────────────────────────────
def test_not_enough_puts_retake_first_and_drops_keep_observing() -> None:
    """못 잰 비교를 "다음에 또 찍어 흐름을 보자" 로 닫으면, 잴 수 없었다는 사실이 덮인다."""
    actions = plan_gait_actions(
        _compare(change_kind="not_enough", flagged_sides=[]), ["keep_observing"]
    )
    assert actions[0] == "same_condition_retake"
    assert "keep_observing" not in actions


@pytest.mark.parametrize(
    "compare",
    [
        _compare(change_kind="both_sides", flagged_sides=["left", "right"]),
        _compare(version_mismatch=True),
    ],
)
def test_condition_check_leads_when_the_two_videos_may_differ(compare) -> None:
    assert plan_gait_actions(compare, ["keep_observing"])[0] == "check_conditions"


def test_both_rules_can_lead_together_in_a_fixed_order() -> None:
    actions = plan_gait_actions(
        _compare(change_kind="not_enough", flagged_sides=[], version_mismatch=True), []
    )
    assert actions == ["same_condition_retake", "check_conditions"]


def test_an_ordinary_comparison_keeps_the_model_order_without_duplicates() -> None:
    assert plan_gait_actions(_compare(), ["keep_observing", "keep_observing"]) == ["keep_observing"]
    assert plan_gait_actions(_compare(), []) == ["keep_observing"]


def test_no_vet_visit_action_exists_at_all() -> None:
    """v1 은 진료 권유를 행동에 두지 않는다 (D-080) — 관찰이 판정으로 되돌아가는 문이다."""
    assert set(GAIT_ACTION_MESSAGES) == {
        "same_condition_retake",
        "keep_observing",
        "check_conditions",
    }
    assert not any(
        term in message for message in GAIT_ACTION_MESSAGES.values() for term in ("병원", "진료")
    )


# ── 5. 해설 가드 ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "text",
    [
        "지난번보다 좋아졌어요",
        "조금 나빠진 것 같아요",
        "많이 호전됐어요",
        "상태가 악화됐어요",
        "관절염일 수 있어요",
        "절뚝이는 것으로 보여요",
        "슬개골 탈구가 의심돼요",
        "통증이 있는 것 같아요",
        "동물병원에서 진료를 받아 보세요",
        "수의사와 상의해 보세요",
        "이동범위가 12px 늘었어요",
        "30% 정도 차이가 나요",
    ],
)
def test_guard_catches_direction_diagnosis_vet_and_numbers(text: str) -> None:
    assert speaks_beyond_change(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "왼쪽 뒷다리에서 차이가 함께 보였어요",
        "고관절과 무릎 쪽에서 관찰됐어요",
        "두 영상의 촬영 조건이 달랐을 수 있어요",
        "3개 지표 중 2개에서 차이가 보였어요",
        "같은 관절을 충분히 재지 못했어요",
    ],
)
def test_guard_leaves_ordinary_change_sentences_alone(text: str) -> None:
    """과잉 차단은 해설을 통째로 고정 문구로 만든다 — 관절 이름과 센 개수는 막지 않는다."""
    assert speaks_beyond_change(text) is False


@pytest.mark.parametrize(
    "text",
    [
        # #575 실측에서 가드가 통째로 지웠던 그 문장.
        "이 결과는 움직임의 변화를 나타낼 뿐, 상태가 좋아지거나 나빠졌다는 의미는 아니에요.",
        "좋아졌는지 나빠졌는지는 이 비교로 말할 수 없어요.",
        "두 영상이 달랐다는 것이지 호전됐다는 뜻은 아니에요.",
        "이 기능은 좋아졌다 나빠졌다를 판단하지 않아요.",
        # gc_v2 실측에서 또 걸렸던 꼴 — `의미하지는 않아요` 는 `의미는 아니` 와 다른 활용이다.
        "이 분석은 움직임의 차이만 보여줄 뿐, 좋아지거나 나빠졌다는 방향성을 의미하지는 않아요.",
        "두 영상이 달랐다는 것이고, 호전을 나타내지는 않아요.",
    ],
)
def test_guard_leaves_a_sentence_that_disclaims_direction(text: str) -> None:
    """방향을 **말하지 않는다고 밝힌** 문장은 방향 주장이 아니다 (#576).

    v1 실측에서 가드가 이런 문장을 갈아 치웠다. 사용자에게 더 나은 문장이 사라지고 일반
    요약으로 대체됐다 — 가드가 좁아서가 아니라 **넓어서** 생긴 오탐이다.
    """
    assert speaks_beyond_change(text) is False


@pytest.mark.parametrize(
    "text",
    [
        # 앞은 부정이고 **뒤가 진짜 주장**이다. 문장에 부정이 있다는 것만으로 봐주면 샌다.
        "좋아졌는지 말할 수는 없지만, 확실히 나아졌어요.",
        "단정할 수 없어요. 그래도 많이 좋아졌어요.",
        "이 비교로는 알 수 없지만 지난번보다 호전된 것 같아요.",
        # `-지 않` 을 통째로 면제하면 이것이 샌다 — 방향을 **주장**하는 부정문이다.
        "예전만큼 좋아지지 않았어요.",
    ],
)
def test_a_disclaimer_does_not_excuse_a_direction_claim_after_it(text: str) -> None:
    """면제는 방향어가 **전부 부정 앞**에 있을 때만이다."""
    assert speaks_beyond_change(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "관절염은 아니에요.",
        "병원에 갈 필요는 없어요.",
        "이동범위가 늘었다고 말할 수는 없어요.",
    ],
)
def test_the_disclaimer_exemption_is_for_direction_only(text: str) -> None:
    """병명 · 진료 · 수치는 **부정해도 해가 남는다** — 면제하지 않는다.

    "관절염은 아니에요" 는 여전히 병명 판단이고, "병원 갈 필요 없어요" 는 여전히 진료
    조언이며, 부정된 수치도 수치다. 실측으로 확인된 오탐도 방향어뿐이었다.
    """
    assert speaks_beyond_change(text) is True


async def test_a_guarded_sentence_is_replaced_whole_and_recorded() -> None:
    async def generate(_prompt: str) -> str:
        return _guide("지난번보다 좋아졌어요.", ["keep_observing"])

    result = await _run(
        GaitCapabilityAdapter(generate=generate),
        {"question": "좋아졌어?", "compare": _compare().model_dump()},
    )
    assert result.status == CapabilityStatus.OK
    assert result.data["guarded"] is True
    assert GAIT_CHANGE_SUMMARY["one_side"] in result.data["answer"]
    assert "좋아졌" not in result.data["answer"]


async def test_a_clean_sentence_is_kept_and_the_notice_is_unconditional() -> None:
    async def generate(_prompt: str) -> str:
        return _guide("왼쪽 뒷다리 쪽에서 차이가 함께 보였어요.", ["keep_observing"])

    result = await _run(
        GaitCapabilityAdapter(generate=generate),
        {"question": "뭐가 달라?", "compare": _compare().model_dump()},
    )
    assert result.data["guarded"] is False
    answer = result.data["answer"]
    assert "왼쪽 뒷다리 쪽에서 차이가 함께 보였어요." in answer
    assert GAIT_ACTION_MESSAGES["keep_observing"] in answer
    assert answer.endswith(GAIT_REFERENCE_NOTICE)
    assert GAIT_VERSION_WARNING not in answer


async def test_the_version_warning_rides_along_whenever_versions_differ() -> None:
    async def generate(_prompt: str) -> str:
        return _guide("왼쪽에서 차이가 보였어요.", ["keep_observing"])

    result = await _run(
        GaitCapabilityAdapter(generate=generate),
        {"question": "뭐가 달라?", "compare": _compare(version_mismatch=True).model_dump()},
    )
    assert GAIT_VERSION_WARNING in result.data["answer"]


# ── 5-2. 전문가 의견 한 줄 (D-080) ───────────────────────────────────────────
def _sides(left_diff: int, right_diff: int, joints: int = 3) -> dict[str, dict[str, Any]]:
    return {
        "left": {"n_joints": joints, "n_diff": left_diff, "n_unmeasured": 0, "flagged": True},
        "right": {"n_joints": joints, "n_diff": right_diff, "n_unmeasured": 0, "flagged": True},
    }


def test_expert_advisory_needs_all_six_points_and_a_sound_comparison() -> None:
    """여섯 중 여섯이 달라졌고, 두 영상 다 충분했고, 버전도 같을 때만이다."""
    assert (
        gait_context._expert_advisory(
            _sides(3, 3), (3, 3), (3, 3), reliability="ok", version_mismatch=False
        )
        is True
    )


@pytest.mark.parametrize(
    ("sides", "left", "right", "reliability", "version_mismatch", "why"),
    [
        (_sides(3, 2), (3, 3), (3, 3), "ok", False, "한쪽이 셋 중 둘만 달라졌다"),
        (_sides(2, 2), (3, 3), (3, 3), "ok", False, "양쪽 다 셋 중 둘"),
        (_sides(3, 3), (3, 3), (3, 3), "recent_short", False, "최근 영상이 짧았다"),
        (_sides(3, 3), (3, 3), (3, 3), "both_short", False, "둘 다 짧았다"),
        (_sides(3, 3), (3, 3), (3, 3), "ok", True, "분석 버전이 다르다"),
        (_sides(2, 2, joints=2), (2, 2), (2, 2), "ok", False, "여섯 지점이 다 비교되지 않았다"),
    ],
)
def test_expert_advisory_stays_off_unless_every_condition_holds(
    sides: dict[str, dict[str, Any]],
    left: tuple[int, int],
    right: tuple[int, int],
    reliability: str,
    version_mismatch: bool,
    why: str,
) -> None:
    """좁게 두는 것이 설계다 — 흔해지면 사용자가 그 줄을 "나빠졌다는 신호" 로 읽는다."""
    assert (
        gait_context._expert_advisory(
            sides, left, right, reliability=reliability, version_mismatch=version_mismatch
        )
        is False
    ), why


async def test_the_advisory_line_rides_along_when_the_flag_is_on() -> None:
    async def generate(_prompt: str) -> str:
        return _guide("양쪽 뒷다리 모두에서 차이가 보였어요.", ["check_conditions"])

    result = await _run(
        GaitCapabilityAdapter(generate=generate),
        {
            "question": "뭐가 달라?",
            "compare": _compare(
                change_kind="both_sides", flagged_sides=["left", "right"], expert_advisory=True
            ).model_dump(),
        },
    )
    answer = result.data["answer"]
    assert GAIT_EXPERT_ADVISORY in answer
    # 기본 고지는 그대로 맨 끝이다 — 이 줄이 그것을 대체하지 않는다.
    assert answer.endswith(GAIT_REFERENCE_NOTICE)
    assert result.data["expert_advisory"] is True


async def test_the_advisory_line_never_leaks_when_the_flag_is_off() -> None:
    async def generate(_prompt: str) -> str:
        return _guide("왼쪽에서 차이가 보였어요.", ["keep_observing"])

    result = await _run(
        GaitCapabilityAdapter(generate=generate),
        {"question": "뭐가 달라?", "compare": _compare().model_dump()},
    )
    assert GAIT_EXPERT_ADVISORY not in result.data["answer"]
    assert result.data["expert_advisory"] is False


def test_the_advisory_says_expert_not_vet_and_claims_no_severity() -> None:
    """**수의사가 아니라 전문가**이고, 심하다 · 악화 · 질환 의심을 말하지 않는다 (D-080).

    진료 권유는 이 능력의 행동 집합에 없다 — 그 선을 이 문장이 넘으면 관찰이 판정으로
    되돌아간다.
    """
    assert "전문가" in GAIT_EXPERT_ADVISORY
    for banned in ("수의사", "병원", "진료", "심각", "악화", "질환", "의심"):
        assert banned not in GAIT_EXPERT_ADVISORY
    # 앞 절이 "영상만으로는 원인을 알 수 없다" 여야 과장으로 안 읽힌다.
    assert GAIT_EXPERT_ADVISORY.startswith("영상만으로는 원인을 알 수 없어요")


def test_the_advisory_does_not_change_the_action_set() -> None:
    """켜져도 행동은 그대로다 — `vet_visit` 는 v1 에 없고, 이 신호가 그것을 여는 문이 아니다."""
    on = _compare(change_kind="both_sides", flagged_sides=["left", "right"], expert_advisory=True)
    off = _compare(change_kind="both_sides", flagged_sides=["left", "right"])
    assert plan_gait_actions(on, ["keep_observing"]) == plan_gait_actions(off, ["keep_observing"])


def test_the_advisory_sentence_would_pass_its_own_guard() -> None:
    """코드가 쓰는 문장이라 가드를 안 지나지만, 지나가도 걸리지 않아야 한다 —
    걸린다면 그 문장이 모델에게 금지한 말을 하고 있다는 뜻이다."""
    assert speaks_beyond_change(GAIT_EXPERT_ADVISORY) is False


# ── 6. 정책 거절 ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("reason", ["diagnosis", "medication", "emergency", "off_topic"])
async def test_a_refusal_uses_the_shared_fixed_sentence(reason: str) -> None:
    async def generate(_prompt: str) -> str:
        return json.dumps({"kind": "refuse", "text": "", "actions": [], "reason": reason})

    result = await _run(
        GaitCapabilityAdapter(generate=generate),
        {"question": "무슨 병이야?", "compare": _compare().model_dump()},
    )
    assert result.status == CapabilityStatus.REFUSED
    assert result.refusal.code == reason
    assert result.refusal.message == SCOPED_REDIRECT_MESSAGES[reason]


# ── 7. 비교 실패 — 고정 문구로 닫고 모델은 안 부른다 ─────────────────────────
@pytest.mark.parametrize("reason", sorted(GAIT_UNAVAILABLE_MESSAGES))
async def test_every_unavailable_reason_closes_without_calling_the_model(reason: str) -> None:
    calls: list[str] = []

    async def generate(prompt: str) -> str:
        calls.append(prompt)
        raise AssertionError("비교가 없으면 모델을 부르지 않는다")

    result = await _run(
        GaitCapabilityAdapter(generate=generate),
        {"question": "왜 안 돼?", "unavailable": reason},
    )
    assert calls == []
    assert result.status == CapabilityStatus.ABSTAINED
    assert result.abstention.code == reason
    assert result.abstention.message == GAIT_UNAVAILABLE_MESSAGES[reason]


def test_the_unavailable_reasons_cover_every_compare_failure() -> None:
    """`services/gait_context` 가 낼 수 있는 이유와 문구 표가 **정확히 같아야** 한다.

    한쪽만 늘면 `KeyError` 로 500 이 나거나, 쓰이지 않는 문구가 남는다.
    """
    from daengs_backend.orchestration.contracts import GaitUnavailableReason

    assert set(GAIT_UNAVAILABLE_MESSAGES) == set(GaitUnavailableReason.__args__)


def test_the_prompt_separates_a_diagnosis_the_owner_already_received() -> None:
    """규칙 8 (#576). v1 에서 그 질문이 **24/24 전부 거절**이었다.

    규칙 3 이 "무슨 병인지 물으면 거절" 이라, 질문 본문에 병명이 들어오는 순간 함께 밀렸다.
    답해야 맞는 자리다 — **진료 결과를 말한 것이지 진단을 요구한 것이 아니다.** 그러면서도
    확인·부정, 병명 따라 쓰기, 병명별 조언, 그리고 **그 진단과 이번 비교를 잇는 것**은
    여전히 막아야 한다. 마지막 하나가 보행에만 필요한 줄이다 — 이으면 관찰이 진단의
    증거로 바뀐다.
    """
    prompt = build_gait_prompt(
        GaitComparePayload.model_validate({"question": "왜?", "compare": _compare().model_dump()})
    )
    rule = prompt.split("8. ", 1)[1].split(chr(10) * 2, 1)[0]
    assert "ALREADY" in rule and "do NOT" in rule
    for phrase in ("confirm or deny", "repeat the name", "advice specific", "cause"):
        assert phrase in rule, phrase
    # gc_v2 에서 모델이 "질환과는 관련이 없어요" 로 답했다 — 링크를 **부인**하려다 규칙 2 의
    # 금지어를 썼고 가드가 그 문장을 통째로 지웠다. 규칙 8 과 가드가 서로 부딪히던 자리라,
    # 부인하지 말고 **비교가 보여주는 것만** 말하라고 못 박는다.
    assert "not even to deny a link" in rule


def test_the_prompt_version_moved_with_the_rule_change() -> None:
    """평가 메타가 이 값을 고정한다 — 안 올리면 새 결과가 옛 셀에 섞인다."""
    assert GAIT_PROMPT_VERSION == "gait-change-ko-v2"


# ── 8. 프로바이더 실패는 격리된다 ────────────────────────────────────────────
async def test_a_timeout_is_a_timeout_not_another_capability() -> None:
    async def generate(_prompt: str) -> str:
        raise TimeoutError

    result = await _run(
        GaitCapabilityAdapter(generate=generate),
        {"question": "뭐가 달라?", "compare": _compare().model_dump()},
    )
    assert result.status == CapabilityStatus.TIMEOUT
    assert result.error.kind == "gait_timeout"


async def test_a_provider_failure_is_contained() -> None:
    async def generate(_prompt: str) -> str:
        raise RuntimeError("boom")

    result = await _run(
        GaitCapabilityAdapter(generate=generate),
        {"question": "뭐가 달라?", "compare": _compare().model_dump()},
    )
    assert result.status == CapabilityStatus.ERROR
    assert result.error.kind == "gait_provider_failure"


async def test_unparseable_output_never_reaches_the_user() -> None:
    async def generate(_prompt: str) -> str:
        return "그냥 문장이에요"

    result = await _run(
        GaitCapabilityAdapter(generate=generate),
        {"question": "뭐가 달라?", "compare": _compare().model_dump()},
    )
    assert result.status == CapabilityStatus.ERROR
    assert result.error.kind == "gait_invalid_output"


@pytest.mark.parametrize(
    "raw",
    [
        {"kind": "guide", "text": "", "actions": [], "reason": None},
        {"kind": "refuse", "text": "", "actions": [], "reason": None},
        {"kind": "guide", "text": "설명이에요.", "actions": ["vet_visit"], "reason": None},
        {"kind": "guide", "text": "설명이에요.", "actions": [], "reason": "diagnosis"},
    ],
)
def test_output_shapes_the_contract_rejects(raw: dict[str, Any]) -> None:
    """`vet_visit` 은 이 능력의 행동이 아니라 **스키마에서** 막힌다."""
    assert validate_gait_guidance(json.dumps(raw)) is None


# ── 9. 프롬프트 ──────────────────────────────────────────────────────────────
def test_the_prompt_carries_the_version_rules_and_the_comparison_only() -> None:
    prompt = build_gait_prompt(GaitComparePayload(question="왼쪽이 왜 변했어?", compare=_compare()))
    assert GAIT_PROMPT_VERSION in prompt
    assert "왼쪽이 왜 변했어?" in prompt
    assert '"change_kind": "one_side"' in prompt
    # 질문이 맨 뒤다 — 규칙이 먼저 서고 사용자 원문이 마지막이다.
    assert prompt.rindex("USER_QUERY") > prompt.rindex("COMPARISON")


# ── 10. 집계 ─────────────────────────────────────────────────────────────────
def test_the_capability_has_a_label_so_aggregation_never_raises() -> None:
    from daengs_backend.orchestration.aggregate import _LABELS

    assert _LABELS[CapabilityName.GAIT] == "보행"


def test_the_engine_registers_the_adapter() -> None:
    from daengs_backend.orchestration.graph import OrchestrationEngine

    engine = OrchestrationEngine()
    assert engine._adapters[CapabilityName.GAIT].capability == CapabilityName.GAIT


def test_the_router_cannot_select_gait_as_an_execute_destination() -> None:
    """라우터 어휘는 건드리지 않았다 — 벤치마크에 영향이 0 이어야 한다."""
    from daengs_backend.orchestration import planner, semantic

    assert "gait" not in planner._EXECUTE_NAMES
    assert "gait" in semantic.HandoffName.__args__
    assert "gait" not in semantic.ExecuteName.__args__


# ── 11. 해소기 ───────────────────────────────────────────────────────────────
class _Row:
    def __init__(self, tier: str | None, captured: datetime.date | None) -> None:
        self.quality_tier = tier
        self.captured_at = captured
        self.created_at = datetime.datetime(2026, 9, 16, tzinfo=datetime.UTC)


@pytest.mark.parametrize(
    ("recent_tier", "past_tier", "expected"),
    [
        ("good", "good", "ok"),
        ("low", "good", "recent_short"),
        ("good", "ok", "past_short"),
        ("low", "low", "both_short"),
        (None, "good", "recent_short"),
    ],
)
def test_reliability_uses_the_same_bar_as_the_comparison(
    recent_tier: str | None, past_tier: str | None, expected: str
) -> None:
    """기준은 저쪽이 참고용 안내를 붙이는 집합과 같다 — 여기서 느슨하면 도장을 찍게 된다."""
    assert (
        gait_context._reliability(past=_Row(past_tier, None), recent=_Row(recent_tier, None))
        == expected
    )


def test_days_between_never_goes_negative() -> None:
    past = _Row("good", datetime.date(2026, 9, 1))
    recent = _Row("good", datetime.date(2026, 9, 15))
    assert gait_context._days_between(past, recent, now=None) == 14
    assert gait_context._days_between(recent, past, now=None) == 0


async def test_a_malformed_reference_closes_as_not_found() -> None:
    resolved = await gait_context.resolve_compare(None, uuid.uuid4(), "not-a-uuid", "also-not")
    assert resolved == {gait_context.UNAVAILABLE_KEY: {"reason": "not_found"}}


async def test_the_same_record_twice_closes_before_touching_the_database() -> None:
    same = uuid.uuid4()
    resolved = await gait_context.resolve_compare(None, uuid.uuid4(), same, same)
    assert resolved == {gait_context.UNAVAILABLE_KEY: {"reason": "same_record"}}


async def test_compare_errors_map_to_their_reason_by_code_not_wording(monkeypatch) -> None:
    """문구가 아니라 **기계용 코드**로 가른다 — 문장을 다듬어도 분류가 안 흔들린다."""
    from daengs_backend.services import gait as gait_service

    async def raises(*_args, **_kwargs):
        raise gait_service.CompareError("문구는 언제든 바뀐다", code="different_pet")

    monkeypatch.setattr(gait_service, "compare_detailed", raises)
    resolved = await gait_context.resolve_compare(None, uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    assert resolved == {gait_context.UNAVAILABLE_KEY: {"reason": "different_pet"}}


async def test_a_missing_record_closes_as_not_found(monkeypatch) -> None:
    from daengs_backend.services import gait as gait_service

    async def raises(*_args, **_kwargs):
        raise gait_service.NotFoundError("record")

    monkeypatch.setattr(gait_service, "compare_detailed", raises)
    resolved = await gait_context.resolve_compare(None, uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    assert resolved == {gait_context.UNAVAILABLE_KEY: {"reason": "not_found"}}


async def test_an_unavailable_comparison_closes_as_quality(monkeypatch) -> None:
    from daengs_backend.services import gait as gait_service

    async def unavailable(*_args, **_kwargs):
        return ({"status": "unavailable", "reason": "..."}, _Row("low", None), _Row("low", None))

    monkeypatch.setattr(gait_service, "compare_detailed", unavailable)
    resolved = await gait_context.resolve_compare(None, uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    assert resolved == {gait_context.UNAVAILABLE_KEY: {"reason": "quality"}}


async def test_a_legacy_comparison_closes_as_legacy_pair(monkeypatch) -> None:
    """옛 기록끼리의 비교에는 `side_summary` 가 아예 없다 — 다리별로 말할 근거가 없다."""
    from daengs_backend.services import gait as gait_service

    async def legacy(*_args, **_kwargs):
        return ({"status": "ok"}, _Row("good", None), _Row("good", None))

    monkeypatch.setattr(gait_service, "compare_detailed", legacy)
    resolved = await gait_context.resolve_compare(None, uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    assert resolved == {gait_context.UNAVAILABLE_KEY: {"reason": "legacy_pair"}}


async def test_a_successful_comparison_is_narrowed_to_the_contract(monkeypatch) -> None:
    from daengs_backend.services import gait as gait_service

    async def ok(*_args, **_kwargs):
        result = {
            "status": "ok",
            "version_warning": "두 기록이 서로 다른 분석 버전으로…",
            # 좁힘이 실제로 도는지 보려고 계약에 없는 것을 일부러 섞는다.
            "joint_movement_range_comparison": {"L_Hip": {"record_a": 1.0}},
            "side_summary": {
                "왼쪽": {"n_joints": 3, "n_diff": 2, "n_unmeasured": 0, "flagged": True},
                "오른쪽": {"n_joints": 3, "n_diff": 0, "n_unmeasured": 1, "flagged": False},
            },
        }
        return (
            result,
            _Row("good", datetime.date(2026, 9, 1)),
            _Row("low", datetime.date(2026, 9, 15)),
        )

    monkeypatch.setattr(gait_service, "compare_detailed", ok)
    resolved = await gait_context.resolve_compare(None, uuid.uuid4(), uuid.uuid4(), uuid.uuid4())

    assert resolved == {
        gait_context.CONTEXT_KEY: {
            "change_kind": "one_side",
            "flagged_sides": ["left"],
            "left_measured": 3,
            "left_joints": 3,
            "right_measured": 2,
            "right_joints": 3,
            "days_between": 14,
            "reliability": "recent_short",
            "version_mismatch": True,
            "expert_advisory": False,
        }
    }
