"""Audit and policy tests for the final reviewed Card 2A benchmark version."""

from __future__ import annotations

from pathlib import Path

from tools.router_benchmark.evaluate import apply_acceptance_gates, evaluate_benchmark
from tools.router_benchmark.prompt_v3 import build_semantic_router_prompt
from tools.router_benchmark.schemas import (
    load_benchmark_config,
    load_gold_cases,
    load_gold_v3_cases,
)
from tools.router_benchmark.semantic_v2 import SemanticRoutingDecision, assemble_route_plan


def test_v3_gold_changes_only_mixed_09_plan_and_rationale() -> None:
    v1 = {case.case_id: case for case in load_gold_cases()}
    v3 = {case.case_id: case for case in load_gold_v3_cases()}
    assert len(v3) == 80
    assert set(v3) == set(v1)
    changed = []
    for case_id, v1_case in v1.items():
        before = v1_case.model_dump(mode="json")
        after = v3[case_id].model_dump(mode="json")
        if before != after:
            changed.append(case_id)
            assert before["case_id"] == after["case_id"]
            assert before["category"] == after["category"]
            assert before["query"] == after["query"]
            assert before["context"] == after["context"]
    assert changed == ["mixed_09"]


def test_mixed_09_corrected_gold_is_walk_plus_gait_only() -> None:
    case = next(case for case in load_gold_v3_cases() if case.case_id == "mixed_09")
    assert [request.capability.value for request in case.gold_route_plan.requests] == ["walk"]
    assert [handoff.target for handoff in case.gold_route_plan.handoffs] == ["gait"]
    assert "훈련 방법을 요청하지 않는다" in case.rationale


def test_v3_prompt_adds_one_generic_walk_boundary_without_gold_example() -> None:
    prompt = build_semantic_router_prompt(query="SENTINEL_QUERY", context={})
    assert "Select Walk only for current environmental walking suitability" in prompt
    source = Path(build_semantic_router_prompt.__code__.co_filename).read_text(encoding="utf-8")
    assert "산책 중 자꾸 앞서가는" not in source
    assert "mixed_07" not in source


def test_corrected_cases_assemble_to_real_card_1_gold_plans() -> None:
    by_id = {case.case_id: case for case in load_gold_v3_cases()}
    decisions = {
        "mixed_07": SemanticRoutingDecision(execute=["training"], handoffs=["gait"]),
        "mixed_09": SemanticRoutingDecision(execute=["walk"], handoffs=["gait"]),
    }
    for case_id, decision in decisions.items():
        case = by_id[case_id]
        assembled = assemble_route_plan(decision, query=case.query, context=case.context)
        assert assembled == case.gold_route_plan


def test_v3_still_uses_the_same_frozen_acceptance_gates() -> None:
    cases = load_gold_v3_cases()
    perfect = evaluate_benchmark(
        cases,
        {case.case_id: [case.gold_route_plan] for case in cases},
        prompt_version="semantic-router-ko-v3",
    )
    verdict = apply_acceptance_gates(perfect.summary, load_benchmark_config())
    assert verdict.verdict == "PASS"
    assert len(verdict.checks) == 15
