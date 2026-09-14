"""LangGraph execution of predefined RoutePlans, without semantic routing."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys

import pytest

from daengs_backend.orchestration.contracts import (
    AssistantStatus,
    CapabilityName,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    ClarifyRequest,
    ErrorDetail,
    Handoff,
    LifePayload,
    OutcomeDetail,
    PendingJob,
    PrincipalContext,
    RoutePlan,
    RouterKind,
    TrainingPayload,
    WalkPayload,
)
from daengs_backend.orchestration.graph import OrchestrationEngine

PRINCIPAL = PrincipalContext(subject="test-user", kind="APP_USER")


class FakeAdapter:
    def __init__(
        self,
        capability: CapabilityName,
        result: CapabilityResult | Exception,
        order: list[CapabilityName] | None = None,
        delay: float = 0,
    ) -> None:
        self.capability = capability
        self.result = result
        self.calls: list[CapabilityRequest] = []
        self.order = order
        self.delay = delay

    async def run(self, request: CapabilityRequest, *, request_id: str) -> CapabilityResult:
        assert request_id == "request-1"
        self.calls.append(request)
        if self.order is not None:
            self.order.append(self.capability)
        if self.delay:
            await asyncio.sleep(self.delay)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def result(capability: CapabilityName, status: CapabilityStatus) -> CapabilityResult:
    kwargs = {
        "capability": capability,
        "status": status,
        "elapsed_ms": 1,
    }
    if status == CapabilityStatus.OK:
        kwargs["data"] = {"answer": f"{capability.value} answer"}
    elif status == CapabilityStatus.ABSTAINED:
        kwargs["abstention"] = OutcomeDetail(code="no_evidence", message="근거 부족")
    elif status == CapabilityStatus.REFUSED:
        kwargs["refusal"] = OutcomeDetail(code="safety", message="통제된 거절 문구")
    elif status == CapabilityStatus.PENDING:
        kwargs["job"] = PendingJob(job_id="job-1", poll="/jobs/job-1")
    elif status in {CapabilityStatus.ERROR, CapabilityStatus.TIMEOUT}:
        kwargs["error"] = ErrorDetail(kind="failure", detail="기능 실패")
    return CapabilityResult(**kwargs)


def request(capability: CapabilityName, *, timeout_ms: int | None = None) -> CapabilityRequest:
    payload = {
        CapabilityName.TRAINING: TrainingPayload(question="훈련 질문"),
        CapabilityName.LIFE: LifePayload(question="생활 질문"),
        CapabilityName.WALK: WalkPayload(lat=37.5, lon=127.0),
    }[capability]
    return CapabilityRequest(capability=capability, payload=payload, timeout_ms=timeout_ms)


def plan(*requests: CapabilityRequest, handoffs: list[Handoff] | None = None) -> RoutePlan:
    return RoutePlan(
        requests=list(requests),
        handoffs=handoffs or [],
        router=RouterKind.DETERMINISTIC,
    )


async def run(engine: OrchestrationEngine, route_plan: RoutePlan):
    return await engine.run(
        route_plan=route_plan,
        query="이미 라우팅된 질의",
        principal=PRINCIPAL,
        request_id="request-1",
    )


@pytest.mark.parametrize(
    "capability",
    [CapabilityName.TRAINING, CapabilityName.LIFE, CapabilityName.WALK],
)
async def test_single_capability_route_executes_once(capability: CapabilityName) -> None:
    adapter = FakeAdapter(capability, result(capability, CapabilityStatus.OK))
    response = await run(OrchestrationEngine({capability: adapter}), plan(request(capability)))
    assert response.status == AssistantStatus.ANSWERED
    assert len(adapter.calls) == 1
    assert [item.capability for item in response.results] == [capability]


async def test_training_and_life_execute_once_in_requested_order() -> None:
    order: list[CapabilityName] = []
    training = FakeAdapter(
        CapabilityName.TRAINING,
        result(CapabilityName.TRAINING, CapabilityStatus.OK),
        order,
    )
    life = FakeAdapter(
        CapabilityName.LIFE,
        result(CapabilityName.LIFE, CapabilityStatus.OK),
        order,
    )
    response = await run(
        OrchestrationEngine({CapabilityName.TRAINING: training, CapabilityName.LIFE: life}),
        plan(request(CapabilityName.TRAINING), request(CapabilityName.LIFE)),
    )
    assert order == [CapabilityName.TRAINING, CapabilityName.LIFE]
    assert len(training.calls) == len(life.calls) == 1
    assert [item.capability for item in response.results] == order


async def test_walk_and_skin_handoff_preserve_both_without_executing_skin() -> None:
    walk = FakeAdapter(CapabilityName.WALK, result(CapabilityName.WALK, CapabilityStatus.OK))
    response = await run(
        OrchestrationEngine({CapabilityName.WALK: walk}),
        plan(
            request(CapabilityName.WALK),
            handoffs=[Handoff(target="skin", reason="image_required")],
        ),
    )
    assert response.status == AssistantStatus.ANSWERED
    assert len(walk.calls) == 1
    assert response.handoffs == [Handoff(target="skin", reason="image_required")]
    assert [item.capability for item in response.results] == [CapabilityName.WALK]


async def test_clarify_executes_no_adapter() -> None:
    adapter = FakeAdapter(
        CapabilityName.TRAINING,
        RuntimeError("must not run"),
    )
    route_plan = RoutePlan(
        clarify=ClarifyRequest(question="위치가 어디인가요?", missing=["location"]),
        router=RouterKind.DETERMINISTIC,
    )
    response = await run(
        OrchestrationEngine({CapabilityName.TRAINING: adapter}),
        route_plan,
    )
    assert response.status == AssistantStatus.CLARIFY
    assert response.results == [] and response.handoffs == []
    assert adapter.calls == []


async def test_handoff_only_route_calls_no_capability() -> None:
    adapter = FakeAdapter(CapabilityName.WALK, RuntimeError("must not run"))
    response = await run(
        OrchestrationEngine({CapabilityName.WALK: adapter}),
        plan(handoffs=[Handoff(target="gait", reason="video_required")]),
    )
    assert response.status == AssistantStatus.HANDOFF
    assert response.results == []
    assert adapter.calls == []


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([CapabilityStatus.OK, CapabilityStatus.OK], AssistantStatus.ANSWERED),
        ([CapabilityStatus.OK, CapabilityStatus.ABSTAINED], AssistantStatus.PARTIAL),
        ([CapabilityStatus.OK, CapabilityStatus.REFUSED], AssistantStatus.PARTIAL),
        ([CapabilityStatus.OK, CapabilityStatus.ERROR], AssistantStatus.PARTIAL),
        ([CapabilityStatus.OK, CapabilityStatus.TIMEOUT], AssistantStatus.PARTIAL),
        ([CapabilityStatus.ABSTAINED, CapabilityStatus.ABSTAINED], AssistantStatus.UNCERTAIN),
        ([CapabilityStatus.REFUSED, CapabilityStatus.REFUSED], AssistantStatus.REFUSED),
        ([CapabilityStatus.ABSTAINED, CapabilityStatus.REFUSED], AssistantStatus.REFUSED),
        ([CapabilityStatus.ERROR, CapabilityStatus.TIMEOUT], AssistantStatus.FAILED),
        ([CapabilityStatus.PENDING, CapabilityStatus.PENDING], AssistantStatus.PENDING),
        ([CapabilityStatus.OK, CapabilityStatus.PENDING], AssistantStatus.PARTIAL),
    ],
)
async def test_aggregation_follows_the_approved_truth_table(
    statuses: list[CapabilityStatus], expected: AssistantStatus
) -> None:
    capabilities = [CapabilityName.TRAINING, CapabilityName.LIFE]
    adapters = {
        capability: FakeAdapter(capability, result(capability, status))
        for capability, status in zip(capabilities, statuses, strict=True)
    }
    response = await run(
        OrchestrationEngine(adapters),
        plan(*(request(capability) for capability in capabilities)),
    )
    assert response.status == expected
    assert [item.status for item in response.results] == statuses


async def test_refusal_wording_is_not_rewritten() -> None:
    training = FakeAdapter(
        CapabilityName.TRAINING,
        result(CapabilityName.TRAINING, CapabilityStatus.REFUSED),
    )
    response = await run(
        OrchestrationEngine({CapabilityName.TRAINING: training}),
        plan(request(CapabilityName.TRAINING)),
    )
    assert response.message == "통제된 거절 문구"


async def test_adapter_exception_becomes_error_without_crashing_graph() -> None:
    training = FakeAdapter(CapabilityName.TRAINING, RuntimeError("boom"))
    response = await run(
        OrchestrationEngine({CapabilityName.TRAINING: training}),
        plan(request(CapabilityName.TRAINING)),
    )
    assert response.status == AssistantStatus.FAILED
    assert response.results[0].status == CapabilityStatus.ERROR
    # The engine's catch-all writes a fixed sentence, never the exception text: an adapter
    # that raises with a query, a path, or a provider payload in the message must not have
    # it reach the user. `kind` keeps the class name so the log still says what broke.
    assert response.results[0].error is not None
    assert "boom" not in response.results[0].error.detail
    assert "boom" not in response.message
    assert response.results[0].error.kind == "RuntimeError"


async def test_missing_adapter_fails_predictably_as_a_capability_error() -> None:
    response = await run(OrchestrationEngine({}), plan(request(CapabilityName.TRAINING)))
    assert response.status == AssistantStatus.FAILED
    assert response.results[0].error
    assert response.results[0].error.kind == "unsupported_capability"


async def test_actual_request_deadline_maps_to_timeout() -> None:
    training = FakeAdapter(
        CapabilityName.TRAINING,
        result(CapabilityName.TRAINING, CapabilityStatus.OK),
        delay=0.05,
    )
    response = await run(
        OrchestrationEngine({CapabilityName.TRAINING: training}),
        plan(request(CapabilityName.TRAINING, timeout_ms=1)),
    )
    assert response.status == AssistantStatus.FAILED
    assert response.results[0].status == CapabilityStatus.TIMEOUT
    assert response.results[0].error and response.results[0].error.kind == "orchestration_timeout"


async def test_raw_auth_credentials_are_rejected_before_graph_state() -> None:
    with pytest.raises(ValueError, match="credentials are forbidden"):
        await OrchestrationEngine({}).run(
            route_plan=plan(handoffs=[Handoff(target="skin", reason="image_required")]),
            query="질의",
            principal=PRINCIPAL,
            context={"nested": {"Authorization": "Bearer secret"}},
        )


def test_constructing_default_engine_does_not_load_training_ml() -> None:
    probe = (
        "import json,sys; "
        "from daengs_backend.orchestration.graph import OrchestrationEngine; "
        "OrchestrationEngine(); "
        "print(json.dumps(sorted(m for m in sys.modules "
        "if m == 'torch' or m.startswith('sentence_transformers'))))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert json.loads(completed.stdout.strip().splitlines()[-1]) == []


# ---------------------------------------------------------------- 이력 절 배선 (#79 3번)
#
# 절 자체는 `test_orchestration_aggregate.py` 가 봅니다. 여기서 보는 것은 **그래프가
# `context["screening_history"]` 를 거기까지 가져다 주는가**, 그리고 그 길이 payload 와
# **같은 화이트리스트**를 지나는가입니다 — 두 경로가 갈라지면 한쪽만 넓어져도 안 깨집니다.


_HISTORY_CONTEXT = {
    "screening_history": [
        {"verdict": "abnormal", "days_ago": 30},
        {"verdict": "normal", "days_ago": 60},
    ]
}


async def _run_with_context(engine: OrchestrationEngine, route_plan: RoutePlan, context: dict):
    return await engine.run(
        route_plan=route_plan,
        query="지난번보다 어때?",
        principal=PRINCIPAL,
        request_id="request-1",
        context=context,
    )


async def test_이력이_핸드오프_응답까지_간다() -> None:
    """"지난번보다 어때요" 의 실제 모양 — 능력은 하나도 안 돌고 skin 핸드오프만 난다."""
    response = await _run_with_context(
        OrchestrationEngine({}),
        plan(handoffs=[Handoff(target="skin", reason="image_upload_required")]),
        _HISTORY_CONTEXT,
    )
    assert response.status == AssistantStatus.HANDOFF
    assert response.message.startswith("[이전 기록] 30일 전 이상 소견 있음 · 60일 전 특이 소견 없음")
    assert "피부 사진을 등록해" in response.message


async def test_능력이_답하면_그래프도_이력을_안_싣는다() -> None:
    life = FakeAdapter(CapabilityName.LIFE, result(CapabilityName.LIFE, CapabilityStatus.OK))
    response = await _run_with_context(
        OrchestrationEngine({CapabilityName.LIFE: life}),
        plan(request(CapabilityName.LIFE)),
        _HISTORY_CONTEXT,
    )
    assert response.status == AssistantStatus.ANSWERED
    assert "[이전 기록]" not in response.message


async def test_이력_없는_요청은_이_카드_이전과_같다() -> None:
    """컨텍스트에 이력이 없으면 응답이 한 글자도 안 달라집니다."""
    args = (
        OrchestrationEngine({}),
        plan(handoffs=[Handoff(target="skin", reason="image_upload_required")]),
    )
    base = await run(*args)
    for context in ({}, {"screening_history": []}, {"active_dog_id": "dog-1"}):
        got = await _run_with_context(*args, context)
        assert got.message == base.message, context


async def test_모양이_틀린_이력은_응답을_안_깬다() -> None:
    """payload 쪽과 같은 판단입니다 — 여기서 터지면 답할 수 있는 요청이 통째로 실패합니다."""
    for bad in ("abnormal", {"verdict": "normal"}, [{"verdict": "확실치_않음", "days_ago": 3}]):
        got = await _run_with_context(
            OrchestrationEngine({}),
            plan(handoffs=[Handoff(target="skin", reason="image_upload_required")]),
            {"screening_history": bad},
        )
        assert got.status == AssistantStatus.HANDOFF
        assert "[이전 기록]" not in got.message, bad


async def test_병변_이름은_이력_절로도_못_간다() -> None:
    """답변 경로도 payload 와 같은 화이트리스트를 지납니다 (불변식 15, D-023)."""
    got = await _run_with_context(
        OrchestrationEngine({}),
        plan(handoffs=[Handoff(target="skin", reason="image_upload_required")]),
        {
            "screening_history": [
                {
                    "verdict": "abnormal",
                    "days_ago": 30,
                    "top1": "구진",
                    "headline": "이상 소견이 있어요",
                    "photo_storage_key": "s3://bucket/key.jpg",
                }
            ]
        },
    )
    assert "[이전 기록] 30일 전 이상 소견 있음" in got.message
    for 금지 in ("구진", "이상 소견이 있어요", "s3://", "photo"):
        assert 금지 not in got.message
