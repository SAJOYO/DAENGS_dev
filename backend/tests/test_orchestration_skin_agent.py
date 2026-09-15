"""피부 판정 해설 서브에이전트 (D-078) — 진입 · 좁힘 · 코드 가드 · 실패 격리. 프로바이더 호출 0.

이 파일이 고정하는 것:

- **진입은 신호 전용이다.** `requested_capability="skin"` 에 서버가 해소한 판정 기록이 붙고 킬
  스위치가 켜져 있을 때만 배타 단일 `skin` 요청이 된다. 하나라도 아니면 같은 신호가 예전 HANDOFF
  다. 라우터는 `skin` 을 못 고른다. 응급은 이것보다 앞이다.
- **payload 는 #307 의 좁은 계약 그대로다.** 컨텍스트에 병변 이름이나 확률이 섞여 와도 payload 에
  칸이 없다.
- **안전 규칙은 코드가 지킨다.** `abnormal` 은 진료 권유가 맨 앞, `retake` 는 다시 찍기가 맨 앞,
  둘 다 "지켜보기" 는 빠진다. 해설에 병변 이름 · 확률이 섞이면 고정 문장으로 바뀐다.
- 거절 · 행동 · 고지 문장은 모델이 아니라 코드가 쓴다. 실패는 ERROR/TIMEOUT 이다.
"""

from __future__ import annotations

import json
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from daengs_backend.config import settings
from daengs_backend.orchestration.adapters.skin import (
    _LESION_TERMS,
    SKIN_PROMPT_VERSION,
    SkinCapabilityAdapter,
    SkinGuidance,
    build_skin_prompt,
    plan_actions,
    speaks_beyond_screening,
    validate_skin_guidance,
)
from daengs_backend.orchestration.aggregate import _SCREENING_VERDICTS, aggregate_results
from daengs_backend.orchestration.contracts import (
    AssistantStatus,
    CapabilityName,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    GeneralPayload,
    PrincipalContext,
    RouterKind,
    ScreeningContext,
    ScreeningHistory,
    SkinPayload,
)
from daengs_backend.orchestration.graph import OrchestrationEngine
from daengs_backend.orchestration.planner import (
    resolve_deterministic_route,
    resolve_emergency_route,
    resolve_skin_route,
)
from daengs_backend.orchestration.redirects import (
    SCOPED_REDIRECT_MESSAGES,
    SKIN_ACTION_MESSAGES,
    SKIN_REFERENCE_NOTICE,
    SKIN_VERDICT_SUMMARY,
    SkinAction,
)
from daengs_backend.orchestration.semantic import ExecuteName, GeminiSemanticRouter
from daengs_backend.orchestration.service import AssistantOrchestrationService
from daengs_screening.config import CLASS_KO, NORMAL_LABEL

PRINCIPAL = PrincipalContext(subject="test-user", kind="APP_USER")
QUERY = "이거 병원 가야 해?"
SCREENED = {
    "screening": {"verdict": "abnormal", "days_ago": 0},
    "screening_history": [{"verdict": "normal", "days_ago": 30}],
}


def skin_route(**overrides: Any):
    arguments: dict[str, Any] = {
        "query": QUERY,
        "context": dict(SCREENED),
        "requested_capability": "skin",
        "enabled": True,
    }
    arguments.update(overrides)
    return resolve_skin_route(**arguments)


def payload(verdict: str = "abnormal", *, history: list[dict] | None = None) -> SkinPayload:
    return SkinPayload(
        question=QUERY,
        screening=ScreeningContext(verdict=verdict, days_ago=0),
        history=ScreeningHistory.model_validate({"entries": history}) if history else None,
    )


def request(verdict: str = "abnormal") -> CapabilityRequest:
    return CapabilityRequest(capability=CapabilityName.SKIN, payload=payload(verdict))


def adapter_returning(raw: object) -> SkinCapabilityAdapter:
    async def generate(_prompt: str) -> object:
        return raw

    return SkinCapabilityAdapter(generate=generate)


def guide(text: str = "이번 사진에서 확인이 필요한 부분이 보였어요.", actions=None) -> dict:
    return {"kind": "guide", "text": text, "actions": actions or [], "reason": None}


# ── 진입: planner ─────────────────────────────────────────────────────


def test_signal_with_a_resolved_record_builds_one_exclusive_skin_request() -> None:
    plan = skin_route()
    assert plan is not None
    [only] = plan.requests
    assert only.capability == CapabilityName.SKIN
    assert plan.handoffs == [] and plan.clarify is None
    assert plan.router is RouterKind.DETERMINISTIC
    assert plan.model is None and plan.prompt_version is None


def test_payload_is_the_narrow_contract_and_nothing_else() -> None:
    context = {
        "screening": {
            "verdict": "abnormal",
            "days_ago": 2,
            # 상류 버그로 섞여 와도 payload 에 닿으면 안 된다 (불변식 15).
            "top1": "A6",
            "probability": 0.93,
            "headline": "결절이 의심돼요",
        },
        "screening_history": [{"verdict": "normal", "days_ago": 40, "top1": "A1"}],
        "dog": {"breed": "푸들"},
        "location": {"lat": 37.5, "lon": 127.0},
    }
    plan = skin_route(context=context)
    assert plan is not None
    dumped = plan.requests[0].payload.model_dump(mode="json")
    assert dumped == {
        "question": QUERY,
        "screening": {"verdict": "abnormal", "days_ago": 2},
        "history": {"entries": [{"verdict": "normal", "days_ago": 40}]},
    }


def test_no_history_leaves_the_field_empty() -> None:
    plan = skin_route(context={"screening": {"verdict": "retake", "days_ago": 0}})
    assert plan is not None
    assert plan.requests[0].payload.history is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"enabled": False},
        {"requested_capability": None},
        {"requested_capability": "life"},
        {"requested_capability": "vet_contact"},
        {"context": {}},
        {"context": {"screening": {"verdict": "unknown", "days_ago": 0}}},
        {"context": {"screening": {"verdict": "abnormal", "days_ago": -1}}},
        {"query": "가" * 1_001},
    ],
)
def test_anything_short_of_all_three_does_not_open_the_agent(overrides: dict) -> None:
    assert skin_route(**overrides) is None


def test_without_a_record_the_same_signal_is_still_the_old_handoff() -> None:
    plan = resolve_deterministic_route(requested_capability="skin", query=QUERY, context={})
    assert plan is not None
    assert plan.requests == []
    assert [(h.target, h.reason) for h in plan.handoffs] == [("skin", "image_upload_required")]


def test_the_explicit_resolver_never_turns_skin_into_an_execute() -> None:
    """`skin` 을 `_EXECUTE_NAMES` 에 넣으면 이 신호가 payload 규칙 없는 EXECUTE 가 되어 500 이 난다."""
    plan = resolve_deterministic_route(
        requested_capability="skin", query=QUERY, context=dict(SCREENED)
    )
    assert plan is not None
    assert plan.requests == [] and [h.target for h in plan.handoffs] == ["skin"]


def test_the_router_cannot_select_skin() -> None:
    assert "skin" not in get_args(ExecuteName)


# ── 계약 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("extra", ["top1", "probability", "lesion", "headline", "dog"])
def test_payload_has_no_room_for_anything_the_screening_did_not_decide(extra: str) -> None:
    with pytest.raises(ValidationError):
        SkinPayload.model_validate(
            {"question": QUERY, "screening": {"verdict": "normal", "days_ago": 0}, extra: "x"}
        )


def test_request_rejects_a_foreign_payload() -> None:
    with pytest.raises((ValidationError, TypeError)):
        CapabilityRequest(capability=CapabilityName.SKIN, payload=GeneralPayload(question=QUERY))


# ── 진입: service 의 순서 ─────────────────────────────────────────────


class _Recorder:
    def __init__(self, capability: CapabilityName) -> None:
        self.capability = capability
        self.seen: list[CapabilityRequest] = []

    async def run(self, request: CapabilityRequest, *, request_id: str) -> CapabilityResult:
        del request_id
        self.seen.append(request)
        return CapabilityResult(
            capability=self.capability,
            status=CapabilityStatus.OK,
            data={"answer": f"{self.capability.value} 답"},
            elapsed_ms=0,
        )


async def _router_must_not_run(*_args: Any, **_kwargs: Any) -> object:
    raise AssertionError("명시 신호로 끝나는 요청이 의미 라우터를 불렀다")


def _service(*recorders: _Recorder) -> AssistantOrchestrationService:
    return AssistantOrchestrationService(
        engine=OrchestrationEngine(adapters={r.capability: r for r in recorders}),
        semantic_router=GeminiSemanticRouter(generate=_router_must_not_run),
    )


async def test_service_runs_the_agent_before_the_explicit_handoff() -> None:
    skin = _Recorder(CapabilityName.SKIN)
    response = await _service(skin).run(
        query=QUERY, principal=PRINCIPAL, context=dict(SCREENED), requested_capability="skin"
    )
    assert response.status is AssistantStatus.ANSWERED
    assert response.handoffs == []
    [seen] = skin.seen
    assert seen.payload.screening.verdict == "abnormal"


async def test_emergency_still_comes_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "skin_agent", True)
    skin, vet = _Recorder(CapabilityName.SKIN), _Recorder(CapabilityName.VET_CONTACT)
    await _service(skin, vet).run(
        query="강아지가 숨을 잘 못 쉬어요",
        principal=PRINCIPAL,
        context=dict(SCREENED),
        requested_capability="skin",
    )
    assert skin.seen == [] and len(vet.seen) == 1


async def test_kill_switch_off_is_the_old_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "skin_agent", False)
    skin = _Recorder(CapabilityName.SKIN)
    response = await _service(skin).run(
        query=QUERY, principal=PRINCIPAL, context=dict(SCREENED), requested_capability="skin"
    )
    assert skin.seen == []
    assert response.status is AssistantStatus.HANDOFF
    assert [h.target for h in response.handoffs] == ["skin"]


def test_the_kill_switch_defaults_on() -> None:
    """켜 둬도 운영이 달라지지 않는 이유는 진입 조건이다 — 기록 id 를 함께 보내는 클라이언트가 없다."""
    assert type(settings).model_fields["skin_agent"].default is True


def test_engine_registers_the_capability_by_default() -> None:
    assert CapabilityName.SKIN in OrchestrationEngine()._adapters


# ── 코드 가드: 행동 ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("verdict", "chosen", "expected"),
    [
        ("abnormal", [], ["vet_visit"]),
        ("abnormal", ["observe"], ["vet_visit"]),
        ("abnormal", ["retake", "vet_visit"], ["vet_visit", "retake"]),
        ("abnormal", ["observe", "retake", "observe"], ["vet_visit", "retake"]),
        ("retake", [], ["retake"]),
        ("retake", ["observe"], ["retake"]),
        ("retake", ["vet_visit", "retake"], ["retake", "vet_visit"]),
        ("normal", [], ["observe"]),
        ("normal", ["observe", "observe"], ["observe"]),
        ("normal", ["vet_visit", "observe"], ["vet_visit", "observe"]),
    ],
)
def test_plan_actions_enforces_the_verdict_rules(
    verdict: str, chosen: list[SkinAction], expected: list[SkinAction]
) -> None:
    assert plan_actions(verdict, chosen) == expected


@pytest.mark.parametrize("verdict", ["abnormal", "retake"])
def test_no_unjudged_or_flagged_photo_ever_ends_in_just_watching(verdict: str) -> None:
    for chosen in (["observe"], ["observe", "observe"], []):
        planned = plan_actions(verdict, chosen)
        assert "observe" not in planned and planned[0] in ("vet_visit", "retake")


# ── 코드 가드: 해설 문장 ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "결절처럼 보여서 걱정되실 수 있어요.",
        "아토피일 가능성도 있어요.",
        "이상일 확률이 높아 보여요.",
        "약 87% 정도로 이상이 보였어요.",
        "90 퍼센트 이상이에요.",
    ],
)
def test_text_that_speaks_beyond_the_verdict_is_caught(text: str) -> None:
    assert speaks_beyond_screening(text)


def test_plain_guidance_passes() -> None:
    assert not speaks_beyond_screening("이번 사진에서 한 번 보여 드리면 좋을 부분이 보였어요.")


def test_lesion_terms_cover_every_screening_class_name() -> None:
    """사본 대조 — 스크리닝 클래스가 늘거나 이름이 바뀌면 여기서 걸린다 (#269 와 같은 장치)."""
    names = {
        term
        for label, name in CLASS_KO.items()
        if label != NORMAL_LABEL
        for term in name.split("·")
    }
    assert names <= set(_LESION_TERMS)


# ── 어댑터 ───────────────────────────────────────────────────────────


async def test_abnormal_answer_leads_with_the_vet_and_ends_with_the_notice() -> None:
    result = await adapter_returning(guide(actions=["observe"])).run(request(), request_id="r")
    assert result.status is CapabilityStatus.OK
    assert result.data["actions"] == ["vet_visit"]
    answer = result.data["answer"]
    assert SKIN_ACTION_MESSAGES["vet_visit"] in answer
    assert SKIN_ACTION_MESSAGES["observe"] not in answer
    assert answer.endswith(SKIN_REFERENCE_NOTICE)
    assert result.data["verdict"] == "abnormal" and result.data["guarded"] is False


async def test_a_lesion_name_replaces_the_text_with_the_fixed_summary() -> None:
    raw = guide(text="궤양이 의심돼서 병원에 가 보시는 게 좋아요.", actions=["vet_visit"])
    result = await adapter_returning(raw).run(request(), request_id="r")
    assert result.status is CapabilityStatus.OK
    assert result.data["guarded"] is True
    assert result.data["answer"].startswith(SKIN_VERDICT_SUMMARY["abnormal"])
    assert "궤양" not in result.data["answer"]


async def test_json_text_output_is_accepted() -> None:
    raw = json.dumps(guide(actions=["retake"]), ensure_ascii=False)
    result = await adapter_returning(raw).run(request("retake"), request_id="r")
    assert result.status is CapabilityStatus.OK and result.data["actions"] == ["retake"]


@pytest.mark.parametrize("reason", ["diagnosis", "medication", "emergency", "off_topic"])
async def test_refusals_use_the_fixed_copy(reason: str) -> None:
    raw = {"kind": "refuse", "text": "", "actions": [], "reason": reason}
    result = await adapter_returning(raw).run(request(), request_id="r")
    assert result.status is CapabilityStatus.REFUSED
    assert result.refusal.code == reason
    assert result.refusal.message == SCOPED_REDIRECT_MESSAGES[reason]


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "not json",
        {"kind": "refuse", "text": "", "actions": []},  # 사유 없는 거절
        {"kind": "guide", "text": "   ", "actions": []},  # 빈 해설
        {"kind": "guide", "text": "x", "actions": ["find_vet"]},  # 닫힌 집합 밖
        {"kind": "guide", "text": "x", "actions": [], "lesion": "A6"},  # 없는 칸
    ],
)
async def test_invalid_output_is_an_error_not_a_surfaced_string(raw: object) -> None:
    result = await adapter_returning(raw).run(request(), request_id="r")
    assert result.status is CapabilityStatus.ERROR
    assert result.error.kind == "skin_invalid_output"


async def test_provider_failure_and_timeout_are_contained() -> None:
    async def boom(_prompt: str) -> object:
        raise RuntimeError("provider down")

    async def slow(_prompt: str) -> object:
        raise TimeoutError

    failed = await SkinCapabilityAdapter(generate=boom).run(request(), request_id="r")
    assert failed.status is CapabilityStatus.ERROR
    assert failed.error.kind == "skin_provider_failure"
    timed_out = await SkinCapabilityAdapter(generate=slow).run(request(), request_id="r")
    assert timed_out.status is CapabilityStatus.TIMEOUT


async def test_wrong_payload_type_is_an_error() -> None:
    wrong = CapabilityRequest.model_construct(
        capability=CapabilityName.SKIN, payload=GeneralPayload(question=QUERY)
    )
    result = await adapter_returning(guide()).run(wrong, request_id="r")
    assert result.status is CapabilityStatus.ERROR and result.error.kind == "invalid_payload"


# ── 프롬프트 ─────────────────────────────────────────────────────────


def test_prompt_carries_the_verdict_history_and_question_only() -> None:
    prompt = build_skin_prompt(payload(history=[{"verdict": "retake", "days_ago": 12}]))
    assert prompt.startswith(f"PROMPT_VERSION: {SKIN_PROMPT_VERSION}\n")
    assert 'SCREENING: {"days_ago": 0, "verdict": "abnormal"}' in prompt
    assert 'HISTORY: [{"days_ago": 12, "verdict": "retake"}]' in prompt
    assert prompt.rstrip().endswith(f"USER_QUERY: {QUERY}")


def test_prompt_without_history_says_so() -> None:
    assert "HISTORY: []" in build_skin_prompt(payload())


def test_prompt_never_names_a_lesion() -> None:
    """규칙 문장이 병변 이름을 예로 들면, 금지하려던 것을 모델에게 가르치게 된다."""
    prompt = build_skin_prompt(payload())
    assert not any(term in prompt for term in _LESION_TERMS)


def test_output_schema_offers_exactly_the_closed_action_set() -> None:
    schema = json.dumps(SkinGuidance.model_json_schema(), ensure_ascii=False)
    for action in get_args(SkinAction):
        assert f'"{action}"' in schema
    assert set(SKIN_ACTION_MESSAGES) == set(get_args(SkinAction))
    assert validate_skin_guidance(guide(actions=["find_vet"])) is None


# ── 문구 ─────────────────────────────────────────────────────────────


def test_fixed_copy_never_says_diagnosis() -> None:
    """스토어 문구와 같은 규칙 — 이 기능은 "진단" 을 하지 않는다."""
    copy = [*SKIN_ACTION_MESSAGES.values(), SKIN_REFERENCE_NOTICE, *SKIN_VERDICT_SUMMARY.values()]
    assert not any("진단" in sentence for sentence in copy)


def test_verdict_summary_speaks_the_same_verdict_words_as_the_answer_clause() -> None:
    """`[이전 기록]` 절과 판정 요약이 같은 판정을 다른 말로 부르면 사용자가 다른 판정으로 읽는다."""
    assert set(SKIN_VERDICT_SUMMARY) == set(_SCREENING_VERDICTS)
    assert "특이 소견" in SKIN_VERDICT_SUMMARY["normal"]
    assert "이상 소견" in SKIN_VERDICT_SUMMARY["abnormal"]
    assert "판정하지 못" in SKIN_VERDICT_SUMMARY["retake"]


# ── 집계 ─────────────────────────────────────────────────────────────


def _skin_plan():
    plan = skin_route()
    assert plan is not None
    return plan


def test_an_answered_skin_turn_does_not_repeat_the_history_clause() -> None:
    history = ScreeningHistory.model_validate({"entries": [{"verdict": "normal", "days_ago": 30}]})
    ok = CapabilityResult(
        capability=CapabilityName.SKIN,
        status=CapabilityStatus.OK,
        data={"answer": "해설"},
        elapsed_ms=1,
    )
    response = aggregate_results(
        request_id="r", route_plan=_skin_plan(), results=[ok], screening_history=history
    )
    assert response.status is AssistantStatus.ANSWERED
    assert response.message == "해설"


def test_a_failed_skin_turn_still_tells_what_the_records_say() -> None:
    history = ScreeningHistory.model_validate({"entries": [{"verdict": "normal", "days_ago": 30}]})
    failed = CapabilityResult(
        capability=CapabilityName.SKIN,
        status=CapabilityStatus.ERROR,
        error={"kind": "skin_provider_failure", "detail": "피부 해설 기능 실행에 실패했습니다."},
        elapsed_ms=1,
    )
    response = aggregate_results(
        request_id="r", route_plan=_skin_plan(), results=[failed], screening_history=history
    )
    assert response.status is AssistantStatus.FAILED
    assert response.message.startswith("[이전 기록] 30일 전 특이 소견 없음")


def test_emergency_resolver_is_untouched_by_the_skin_signal() -> None:
    """`skin` 신호는 응급 게이트를 열지도 막지도 않는다 — 응급 판정은 원문 어휘로만 한다."""
    assert (
        resolve_emergency_route(
            query=QUERY, context=dict(SCREENED), requested_capability="skin", at_night=False
        )
        is None
    )
