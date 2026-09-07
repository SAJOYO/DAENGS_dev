"""Approved orchestration contract invariants."""

from typing import get_args

import pytest
from pydantic import ValidationError

from daengs_backend.orchestration.contracts import (
    CapabilityName,
    CapabilityRequest,
    CapabilityStatus,
    ClarifyRequest,
    Handoff,
    LifePayload,
    PlacePayload,
    RoutePlan,
    RouterKind,
    TrainingPayload,
)
from daengs_backend.orchestration.semantic import ExecuteName
from daengs_backend.schemas.chat import AgentCategory


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


def test_place_payload_contains_original_query_and_location_but_no_identity() -> None:
    payload = PlacePayload(query="  조용한 곳  ", lat=37.5563, lon=126.9236)
    assert payload.query == "  조용한 곳  "
    assert set(payload.model_dump()) == {"query", "lat", "lon"}
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PlacePayload.model_validate(
            {
                "query": "조용한 곳",
                "lat": 37.5563,
                "lon": 126.9236,
                "active_dog_id": "dog-1",
            }
        )


# ---------------------------------------------- 능력 이름의 사본 (#269)


def test_capability_names_have_exactly_three_copies_and_they_agree() -> None:
    """능력 이름 목록이 저장소에 세 벌 있고, 서로를 참조하지 않는다.

    #196 이 `CapabilityName` 에 `place` 를 넣었고, #204 가 `ExecuteName` 을 따라 넓혔고,
    `AgentCategory` 는 아무도 못 봤다. 사본끼리 대조하는 테스트가 없어서 한쪽만 넓혀도
    아무것도 깨지지 않았고, 결국 저장된 `place` 행을 읽을 때 응답 검증이 터져 `/app/chats`
    가 500 을 냈다 (#269). 값 하나가 빠졌다는 것보다 **대조하는 사람이 없었다**는 것이
    사고의 본질이라, 셋을 한자리에서 묶는다.
    """
    names = {capability.value for capability in CapabilityName}
    # `general` (D-057) 은 v9 부터 라우터 목적지이기도 하다 — 세 사본이 다시 완전히 같다.
    assert names == set(get_args(ExecuteName)), "라우터가 고를 수 있는 목적지가 어긋났다"
    assert names == set(get_args(AgentCategory)), "저장된 대화를 읽어 줄 꼬리표가 어긋났다"
