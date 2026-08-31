"""Approved orchestration contract invariants."""

import pytest
from pydantic import ValidationError

from daengs_backend.orchestration.contracts import (
    CapabilityRequest,
    CapabilityStatus,
    ClarifyRequest,
    Handoff,
    LifePayload,
    RoutePlan,
    RouterKind,
    TrainingPayload,
)


def test_abstained_and_refused_are_distinct() -> None:
    assert CapabilityStatus.ABSTAINED != CapabilityStatus.REFUSED


def test_error_and_timeout_are_distinct() -> None:
    assert CapabilityStatus.ERROR != CapabilityStatus.TIMEOUT


def test_requests_and_handoffs_can_coexist() -> None:
    plan = RoutePlan(
        requests=[
            CapabilityRequest(
                capability="life",
                payload=LifePayload(question="동물등록 변경 기한은?"),
            )
        ],
        handoffs=[Handoff(target="skin", reason="image_required")],
        router=RouterKind.DETERMINISTIC,
    )
    assert len(plan.requests) == 1
    assert plan.handoffs[0].target == "skin"


def test_clarify_is_exclusive_at_the_contract_boundary() -> None:
    with pytest.raises(ValidationError, match="clarify is exclusive"):
        RoutePlan(
            requests=[
                CapabilityRequest(
                    capability="training",
                    payload=TrainingPayload(question="입질 교육"),
                )
            ],
            clarify=ClarifyRequest(question="어느 상황인가요?", missing=["situation"]),
            router=RouterKind.DETERMINISTIC,
        )


def test_route_plan_has_no_scalar_mode() -> None:
    assert "mode" not in RoutePlan.model_fields
    with pytest.raises(ValidationError):
        RoutePlan(mode="EXECUTE", router=RouterKind.DETERMINISTIC)


def test_unsupported_capability_fails_predictably() -> None:
    with pytest.raises(ValidationError):
        CapabilityRequest(capability="skin", payload={"question": "피부가 이상해"})


def test_capability_payload_must_match_its_identity() -> None:
    with pytest.raises(ValidationError):
        CapabilityRequest(
            capability="training",
            payload=LifePayload(question="훈련 질문"),
        )
