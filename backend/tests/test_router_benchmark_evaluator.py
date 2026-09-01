"""Deterministic metric, retry and hard-gate tests using fake predictions only."""

from __future__ import annotations

from copy import deepcopy

import pytest

from daengs_backend.orchestration.contracts import RoutePlan
from tools.router_benchmark.evaluate import apply_acceptance_gates, evaluate_benchmark
from tools.router_benchmark.schemas import (
    GoldCase,
    PerformanceObservation,
    load_benchmark_config,
    load_gold_cases,
)


def _cases(*case_ids: str) -> list[GoldCase]:
    wanted = set(case_ids)
    return [case for case in load_gold_cases() if case.case_id in wanted]


def _empty_plan() -> dict:
    return {
        "requests": [],
        "handoffs": [],
        "clarify": None,
        "router": "llm",
        "model": "gemini-3.5-flash-lite",
    }


def _semantic_misroute(plan: RoutePlan, capability: str) -> dict:
    raw = plan.model_dump(mode="json")
    raw["requests"] = [
        {
            "capability": capability,
            "payload": {"question": "오분류된 유효 출력"},
            "timeout_ms": None,
        }
    ]
    raw["handoffs"] = []
    raw["clarify"] = None
    return raw


def test_exact_semantic_match_normalizes_observation_and_presentation_text() -> None:
    case = _cases("mixed_01")[0]
    predicted = case.gold_route_plan.model_dump(mode="json")
    predicted["router"] = "deterministic"
    predicted["model"] = None
    predicted["handoffs"][0]["reason"] = "다른 사용자 표시 문구"
    result = evaluate_benchmark([case], {case.case_id: [predicted]})
    assert result.results[0].exact_match is True


def test_exact_match_does_not_ignore_execute_handoff_or_clarify_differences() -> None:
    execute_case, handoff_case, clarify_case = _cases("training_01", "handoff_01", "clarify_01")
    evaluation = evaluate_benchmark(
        [execute_case, handoff_case, clarify_case],
        {
            execute_case.case_id: [_empty_plan()],
            handoff_case.case_id: [_empty_plan()],
            clarify_case.case_id: [_empty_plan()],
        },
    )
    assert [result.exact_match for result in evaluation.results] == [False, False, False]


def test_executable_micro_and_per_capability_precision_recall_are_case_sensitive() -> None:
    training_case, multi_case = _cases("training_01", "multi_01")
    evaluation = evaluate_benchmark(
        [training_case, multi_case],
        {
            training_case.case_id: [_semantic_misroute(training_case.gold_route_plan, "life")],
            multi_case.case_id: [
                {
                    **multi_case.gold_route_plan.model_dump(mode="json"),
                    "requests": multi_case.gold_route_plan.model_dump(mode="json")["requests"][:1],
                }
            ],
        },
    )
    summary = evaluation.summary
    assert summary.executable_precision == pytest.approx(0.5)
    assert summary.executable_recall == pytest.approx(1 / 3)
    assert summary.training_precision == pytest.approx(1.0)
    assert summary.training_recall == pytest.approx(0.5)
    assert summary.life_precision == pytest.approx(0.0)
    assert summary.life_recall == pytest.approx(0.0)


def test_multi_execute_recall_and_exact_set_accuracy() -> None:
    cases = _cases("multi_01", "multi_11")
    first = cases[0].gold_route_plan.model_dump(mode="json")
    first["requests"] = first["requests"][:1]
    attempts = {
        cases[0].case_id: [first],
        cases[1].case_id: [cases[1].gold_route_plan],
    }
    summary = evaluate_benchmark(cases, attempts).summary
    assert summary.multi_execute_recall == pytest.approx(4 / 5)
    assert summary.exact_executable_set_accuracy_multi == pytest.approx(0.5)


def test_handoff_precision_recall_and_skin_gait_recall() -> None:
    skin_case, gait_case = _cases("handoff_01", "handoff_06")
    wrong_target = gait_case.gold_route_plan.model_dump(mode="json")
    wrong_target["handoffs"][0]["target"] = "skin"
    summary = evaluate_benchmark(
        [skin_case, gait_case],
        {skin_case.case_id: [skin_case.gold_route_plan], gait_case.case_id: [wrong_target]},
    ).summary
    assert summary.handoff_precision == pytest.approx(0.5)
    assert summary.handoff_recall == pytest.approx(0.5)
    assert summary.skin_handoff_recall == pytest.approx(1.0)
    assert summary.gait_handoff_recall == pytest.approx(0.0)


def test_clarify_precision_recall_and_false_positive_rate() -> None:
    by_id = {case.case_id: case for case in _cases("clarify_01", "training_01")}
    clarify_case = by_id["clarify_01"]
    execute_case = by_id["training_01"]
    false_clarify = clarify_case.gold_route_plan.model_dump(mode="json")
    summary = evaluate_benchmark(
        [clarify_case, execute_case],
        {
            clarify_case.case_id: [_empty_plan()],
            execute_case.case_id: [false_clarify],
        },
    ).summary
    assert summary.clarify_precision == pytest.approx(0.0)
    assert summary.clarify_recall == pytest.approx(0.0)
    assert summary.false_positive_clarify_rate == pytest.approx(1.0)


def test_forbidden_and_invented_execute_names_are_detected_from_invalid_attempts() -> None:
    skin_case, gait_case, medical_case = _cases("handoff_01", "handoff_06", "handoff_07")
    invalid_skin = {
        **_empty_plan(),
        "requests": [{"capability": "skin", "payload": {"question": "x"}}],
    }
    invalid_place = {
        **_empty_plan(),
        "requests": [{"capability": "place", "payload": {"question": "x"}}],
    }
    unsupported_handoff = {
        **_empty_plan(),
        "handoffs": [{"target": "medical", "reason": "unapproved"}],
    }
    summary = evaluate_benchmark(
        [skin_case, gait_case, medical_case],
        {
            skin_case.case_id: [invalid_skin, skin_case.gold_route_plan],
            gait_case.case_id: [invalid_place, gait_case.gold_route_plan],
            medical_case.case_id: [unsupported_handoff],
        },
    ).summary
    assert summary.forbidden_execute_count == 1
    assert summary.invented_unsupported_capability_count == 3


def test_schema_retry_accounting_distinguishes_recovered_and_unrecovered() -> None:
    valid_case, recovered_case, failed_case = _cases("training_01", "life_01", "walk_01")
    evaluation = evaluate_benchmark(
        [valid_case, recovered_case, failed_case],
        {
            valid_case.case_id: [valid_case.gold_route_plan],
            recovered_case.case_id: [{}, recovered_case.gold_route_plan],
            failed_case.case_id: ["not-json", {}],
        },
    )
    summary = evaluation.summary
    assert summary.first_pass_schema_valid_rate == pytest.approx(1 / 3)
    assert summary.retry_recovery_count == 1
    assert summary.final_schema_valid_rate == pytest.approx(2 / 3)
    assert summary.unrecovered_schema_failure_count == 1


def test_valid_semantic_misroute_does_not_trigger_schema_retry() -> None:
    case = _cases("training_01")[0]
    misroute = _semantic_misroute(case.gold_route_plan, "life")
    result = evaluate_benchmark([case], {case.case_id: [misroute]})
    assert result.results[0].first_pass_schema_valid is True
    assert result.results[0].retry_used is False
    assert result.results[0].exact_match is False
    with pytest.raises(ValueError, match="must not be retried"):
        evaluate_benchmark([case], {case.case_id: [misroute, case.gold_route_plan]})


def test_invalid_first_attempt_requires_exactly_one_retry() -> None:
    case = _cases("training_01")[0]
    with pytest.raises(ValueError, match="requires exactly one retry"):
        evaluate_benchmark([case], {case.case_id: [{}]})
    with pytest.raises(ValueError, match="one attempt"):
        evaluate_benchmark([case], {case.case_id: [{}, {}, {}]})


def test_frozen_hard_gates_produce_pass_fail_not_a_winner() -> None:
    cases = load_gold_cases()
    perfect = evaluate_benchmark(
        cases, {case.case_id: [deepcopy(case.gold_route_plan)] for case in cases}
    )
    verdict = apply_acceptance_gates(perfect.summary, load_benchmark_config())
    assert verdict.verdict == "PASS"
    assert all(check.passed for check in verdict.checks)
    assert {check.operator for check in verdict.checks} == {">=", "<="}

    degraded_summary = perfect.summary.model_copy(update={"exact_route_plan_match": 0.89})
    failed = apply_acceptance_gates(degraded_summary, load_benchmark_config())
    assert failed.verdict == "FAIL"
    assert [check.metric for check in failed.checks if not check.passed] == [
        "exact_route_plan_match"
    ]


def test_phase_2_performance_fields_are_observational() -> None:
    first, second = _cases("training_01", "life_01")
    evaluation = evaluate_benchmark(
        [first, second],
        {first.case_id: [first.gold_route_plan], second.case_id: [second.gold_route_plan]},
        {
            first.case_id: PerformanceObservation(
                latency_ms=10, input_tokens=100, output_tokens=20, total_tokens=120
            ),
            second.case_id: PerformanceObservation(
                latency_ms=20, input_tokens=110, output_tokens=30, total_tokens=140
            ),
        },
    )
    assert evaluation.performance.warm_sample_count == 2
    assert evaluation.performance.warm_p50_ms == pytest.approx(15)
    assert evaluation.performance.warm_p95_ms == pytest.approx(19.5)
    assert evaluation.performance.input_tokens == 210
    assert evaluation.performance.output_tokens == 50
    assert evaluation.performance.total_tokens == 260
    assert "warm_p50_ms" not in load_benchmark_config().acceptance_gates
