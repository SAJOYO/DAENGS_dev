"""응급 경로의 계획 — 배타 실행, 되묻지 않기, 두 진입점의 동치성."""

from __future__ import annotations

from daengs_backend.orchestration.contracts import CapabilityName, RouterKind
from daengs_backend.orchestration.planner import resolve_emergency_route

SEOUL = {"location": {"lat": 37.5665, "lon": 126.978}}


def test_lexicon_gate_produces_a_single_vet_contact_request() -> None:
    plan = resolve_emergency_route(
        query="우리 보리가 갑자기 경련을 일으켜요",
        context=SEOUL,
        requested_capability=None,
        at_night=False,
    )
    assert plan is not None
    assert [request.capability for request in plan.requests] == [CapabilityName.VET_CONTACT]
    assert plan.clarify is None
    assert plan.handoffs == []
    assert plan.router is RouterKind.DETERMINISTIC
    assert plan.model is None


def test_explicit_signal_builds_the_identical_plan() -> None:
    """두 진입점이 다른 계획을 내면 사용자가 같은 상황에서 다른 답을 받는다."""
    by_lexicon = resolve_emergency_route(
        query="우리 보리가 갑자기 경련을 일으켜요",
        context=SEOUL,
        requested_capability=None,
        at_night=True,
    )
    by_signal = resolve_emergency_route(
        query="우리 보리가 갑자기 경련을 일으켜요",
        context=SEOUL,
        requested_capability="vet_contact",
        at_night=True,
    )
    assert by_lexicon == by_signal


def test_explicit_signal_works_even_when_the_lexicon_does_not_fire() -> None:
    plan = resolve_emergency_route(
        query="병원 좀",
        context=SEOUL,
        requested_capability="vet_contact",
        at_night=False,
    )
    assert plan is not None
    assert [request.capability for request in plan.requests] == [CapabilityName.VET_CONTACT]


def test_missing_coordinates_still_execute_and_never_clarify() -> None:
    plan = resolve_emergency_route(
        query="강아지가 숨을 잘 못 쉬어요",
        context={},
        requested_capability=None,
        at_night=False,
    )
    assert plan is not None
    assert plan.clarify is None
    [request] = plan.requests
    assert request.payload.lat is None
    assert request.payload.lon is None


def test_out_of_box_coordinates_are_treated_as_absent() -> None:
    """신뢰하지 않는 좌표는 좌표가 아니다 (D-051 ③)."""
    plan = resolve_emergency_route(
        query="강아지가 숨을 잘 못 쉬어요",
        context={"location": {"lat": 10.0, "lon": 126.978}},
        requested_capability=None,
        at_night=False,
    )
    assert plan is not None
    [request] = plan.requests
    assert request.payload.lat is None


def test_at_night_reaches_the_payload() -> None:
    plan = resolve_emergency_route(
        query="강아지가 경련을 일으켜요",
        context=SEOUL,
        requested_capability=None,
        at_night=True,
    )
    [request] = plan.requests
    assert request.payload.at_night is True


def test_vet_contact_is_not_a_shared_assembler_destination() -> None:
    """공용 조립기가 이 능력의 payload 를 만들면 안 된다 (D-051 ②).

    `_EXECUTE_NAMES` 에 이름이 새면 명시 신호가 `resolve_deterministic_route` 로 흘러
    `_payload_for` 의 raise 에 걸린다. 지금 안 터지는 것은 순서 덕분이라, 계약으로 잰다.
    """
    from daengs_backend.orchestration.planner import _EXECUTE_NAMES

    assert "vet_contact" not in _EXECUTE_NAMES


def test_non_emergency_returns_none_so_normal_routing_continues() -> None:
    assert (
        resolve_emergency_route(
            query="근처 동물병원 찾아줘",
            context=SEOUL,
            requested_capability=None,
            at_night=False,
        )
        is None
    )
