"""The frozen Place acceptance set (PR #204) — validated without a provider call.

`gold_place_v1.jsonl` is the bounded Place gold set required before the semantic
router may select Place (`docs/place/discovery-migration.md` §7 PR7). It is a SEPARATE
file from the frozen 80-case `gold_v1.jsonl`, which gains no cases: a Place miss and a
v6 routing regression must never be readable as the same number.

Everything here is deterministic. The paid run (`runner_v8.py`) certifies the live
classifier against these same cases; this file certifies that the cases themselves are
well-formed, that they cover the approved contract, and — most importantly — that each
frozen `gold_route_plan` is still exactly what the production planner builds. Gold that
can drift away from the planner is gold that silently stops testing anything.
"""

from __future__ import annotations

import pytest

from daengs_backend.orchestration.contracts import RouterKind
from daengs_backend.orchestration.planner import assemble_route_plan
from daengs_backend.orchestration.semantic import (
    PROMPT_VERSION,
    ROUTER_MODEL_ID,
    SemanticRoutingDecision,
)
from tools.router_benchmark.schemas import load_gold_cases, load_gold_place_cases

# The decision each case asserts the classifier should return. Deliberately restated
# here rather than read out of the gold file: recovering it from the stored plan is
# impossible for the CLARIFY cases (an exclusive CLARIFY erases the execute list), and
# an independent restatement is what makes the plan check an actual cross-check.
EXPECTED_DECISIONS: dict[str, tuple[list[str], list[str]]] = {
    "place_01": (["place"], []),
    "place_02": (["place"], []),
    "place_03": (["place"], []),
    "place_04": (["place"], []),
    "place_mixed_01": (["place", "walk"], []),
    "place_mixed_02": (["place", "walk"], []),
    "place_negative_01": (["walk"], []),
    "place_negative_02": (["training"], []),
    "place_negative_03": ([], []),
    "place_negative_04": ([], []),
    "place_negative_05": (["place"], []),
    "place_clarify_01": (["place"], []),
    "place_clarify_02": (["place", "walk"], []),
    "place_region_01": (["place"], []),
    "place_region_02": (["place"], []),
}


def _by_id():
    return {case.case_id: case for case in load_gold_place_cases()}


def test_gold_set_is_well_formed_and_bounded() -> None:
    cases = load_gold_place_cases()
    assert len(cases) == 15
    ids = [case.case_id for case in cases]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    assert set(ids) == set(EXPECTED_DECISIONS)


def test_the_place_gold_set_does_not_touch_the_frozen_eighty() -> None:
    """The v1 regression baseline is untouched — no Place case was smuggled into it."""
    frozen = load_gold_cases()
    assert len(frozen) == 80
    for case in frozen:
        capabilities = {request.capability.value for request in case.gold_route_plan.requests}
        assert "place" not in capabilities
    # And the two sets share no case id, so a summary can never merge them by accident.
    assert not {c.case_id for c in frozen} & set(EXPECTED_DECISIONS)


@pytest.mark.parametrize("case_id", sorted(EXPECTED_DECISIONS))
def test_gold_plan_is_exactly_what_the_production_planner_builds(case_id: str) -> None:
    """Frozen expectation vs. live assembler — they must not drift apart."""
    case = _by_id()[case_id]
    execute, handoffs = EXPECTED_DECISIONS[case_id]
    rebuilt = assemble_route_plan(
        SemanticRoutingDecision(execute=execute, handoffs=handoffs),
        query=case.query,
        context=dict(case.context),
        router=RouterKind.LLM,
        model=ROUTER_MODEL_ID,
    )
    # gold 파일은 `prompt_version` 을 안 들고 있다 — #238 이 그 필드를 더하기 전에 얼었다.
    # 관측 메타데이터라 라우팅 결정은 하나도 안 달라지므로, 파일을 고치는 대신 여기서 맞춘다.
    assert rebuilt == case.gold_route_plan.model_copy(
        update={"prompt_version": PROMPT_VERSION}
    )


# --------------------------------------------------------- the approved contract rows


def test_the_required_routing_rows_are_covered() -> None:
    cases = _by_id()

    def capabilities(case_id: str) -> set[str]:
        return {r.capability.value for r in cases[case_id].gold_route_plan.requests}

    assert capabilities("place_01") == {"place"}  # 산책하기 좋은 곳 추천해줘
    assert capabilities("place_02") == {"place"}  # 근처 동물병원 찾아줘
    assert capabilities("place_mixed_01") == {"place", "walk"}  # 오늘 …좋은 곳
    assert capabilities("place_negative_01") == {"walk"}  # 오늘 공원 산책 괜찮아?
    assert capabilities("place_negative_02") == {"training"}
    assert capabilities("place_negative_03") == set()  # 목욕은 몇 주마다
    assert capabilities("place_negative_04") == set()


@pytest.mark.parametrize("case_id", ["place_clarify_01", "place_clarify_02"])
def test_clarify_cases_execute_nothing(case_id: str) -> None:
    plan = _by_id()[case_id].gold_route_plan
    assert plan.clarify is not None
    assert plan.clarify.missing == ["location.lat", "location.lon"]
    assert plan.requests == [] and plan.handoffs == []


# -------------------------------------------------------- named region — Option B


@pytest.mark.parametrize("case_id", ["place_region_01", "place_region_02"])
def test_named_region_cases_assert_device_coordinates_and_never_geocoding(case_id: str) -> None:
    """Option B is asserted as *what did not happen*.

    The gold row must not encode any claim that the region was resolved: Place runs on
    the device's own coordinates, and the region survives only as text inside the
    preserved query.
    """
    case = _by_id()[case_id]
    [request] = case.gold_route_plan.requests
    assert request.capability.value == "place"
    # The device coordinates from the case context — never a coordinate for the region.
    assert (request.payload.lat, request.payload.lon) == (
        case.context["location"]["lat"],
        case.context["location"]["lon"],
    )
    assert request.payload.query == case.query
    # The rationale documents the deferral rather than asserting a geocode happened.
    for forbidden in ("지오코딩했", "좌표로 변환했", "그 지역에서 찾았"):
        assert forbidden not in case.rationale
