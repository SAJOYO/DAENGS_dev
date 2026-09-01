"""Deterministic RoutePlan evaluator for Card 2A; no model or capability calls."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from daengs_backend.orchestration.contracts import RoutePlan

from .schemas import (
    AcceptanceVerdict,
    AttemptValidation,
    BenchmarkConfig,
    BenchmarkEvaluation,
    BenchmarkPerformanceSummary,
    BenchmarkSummary,
    CaseResult,
    GateCheck,
    GoldCase,
    PerformanceObservation,
    PromptVersion,
)

PROMPT_VERSION = "semantic-router-ko-v1"
MODEL_ID = "gemini-3.5-flash-lite"
ALLOWED_EXECUTE = frozenset({"training", "life", "walk"})
FORBIDDEN_EXECUTE = frozenset({"skin", "gait"})
ALLOWED_HANDOFFS = frozenset({"skin", "gait"})
_MAXIMUM_GATES = frozenset({"forbidden_execute_count", "invented_unsupported_capability_count"})


def validate_prediction(raw: object) -> AttemptValidation:
    """Validate one provider attempt while retaining only normalized error metadata."""
    parsed: object = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return AttemptValidation(schema_valid=False, error_category="invalid_json")
    capability_names = _raw_capability_names(parsed)
    handoff_names = _raw_handoff_names(parsed)
    try:
        plan = parsed if isinstance(parsed, RoutePlan) else RoutePlan.model_validate(parsed)
    except (ValidationError, ValueError, TypeError):
        return AttemptValidation(
            schema_valid=False,
            requested_capability_names=capability_names,
            handoff_target_names=handoff_names,
            error_category="schema_validation_error",
        )
    return AttemptValidation(
        schema_valid=True,
        plan=plan,
        requested_capability_names=capability_names,
        handoff_target_names=handoff_names,
    )


def evaluate_benchmark(
    gold_cases: Sequence[GoldCase],
    attempts_by_case: Mapping[str, Sequence[AttemptValidation | object]],
    performance_by_case: Mapping[str, PerformanceObservation] | None = None,
    *,
    prompt_version: PromptVersion = PROMPT_VERSION,
    model_id: str = MODEL_ID,
) -> BenchmarkEvaluation:
    """Evaluate final plans and retry behavior against gold without an LLM judge."""
    case_ids = [case.case_id for case in gold_cases]
    if set(case_ids) != set(attempts_by_case):
        missing = sorted(set(case_ids) - set(attempts_by_case))
        extra = sorted(set(attempts_by_case) - set(case_ids))
        raise ValueError(f"prediction case IDs differ: missing={missing}, extra={extra}")

    performance_by_case = performance_by_case or {}
    rows: list[tuple[GoldCase, list[AttemptValidation], CaseResult]] = []
    for case in gold_cases:
        attempts = [
            item if isinstance(item, AttemptValidation) else validate_prediction(item)
            for item in attempts_by_case[case.case_id]
        ]
        _validate_retry_contract(attempts)
        final = attempts[-1]
        prediction = final.plan
        execute_gold = _execute_counter(case.gold_route_plan)
        execute_pred = _execute_counter(prediction)
        handoff_gold = _handoff_counter(case.gold_route_plan)
        handoff_pred = _handoff_counter(prediction)
        performance = performance_by_case.get(case.case_id, PerformanceObservation())
        result = CaseResult(
            case_id=case.case_id,
            prompt_version=prompt_version,
            model=model_id,
            attempt_count=len(attempts),
            first_pass_schema_valid=attempts[0].schema_valid,
            final_schema_valid=final.schema_valid,
            retry_used=len(attempts) == 2,
            prediction=prediction,
            exact_match=_semantic_plan_key(case.gold_route_plan) == _semantic_plan_key(prediction),
            execute_precision=_precision(execute_gold, execute_pred),
            execute_recall=_recall(execute_gold, execute_pred),
            handoff_precision=_precision(handoff_gold, handoff_pred),
            handoff_recall=_recall(handoff_gold, handoff_pred),
            clarify_gold=case.gold_route_plan.clarify is not None,
            clarify_predicted=prediction is not None and prediction.clarify is not None,
            latency_ms=performance.latency_ms,
            input_tokens=performance.input_tokens,
            output_tokens=performance.output_tokens,
            total_tokens=performance.total_tokens,
            error_category=final.error_category,
        )
        rows.append((case, attempts, result))

    summary = _summarize(rows)
    results = [row[2] for row in rows]
    return BenchmarkEvaluation(
        results=results,
        summary=summary,
        performance=_summarize_performance(results),
    )


def apply_acceptance_gates(summary: BenchmarkSummary, config: BenchmarkConfig) -> AcceptanceVerdict:
    """Apply the frozen thresholds; this produces PASS/FAIL, never a model ranking."""
    values = summary.model_dump()
    checks: list[GateCheck] = []
    for metric, threshold in config.acceptance_gates.items():
        if metric not in values:
            raise ValueError(f"acceptance gate references unknown metric: {metric}")
        actual = float(values[metric])
        threshold_value = float(threshold)
        maximum = metric in _MAXIMUM_GATES
        checks.append(
            GateCheck(
                metric=metric,
                operator="<=" if maximum else ">=",
                threshold=threshold_value,
                actual=actual,
                passed=actual <= threshold_value if maximum else actual >= threshold_value,
            )
        )
    return AcceptanceVerdict(
        verdict="PASS" if all(check.passed for check in checks) else "FAIL",
        checks=checks,
    )


def _validate_retry_contract(attempts: Sequence[AttemptValidation]) -> None:
    if not 1 <= len(attempts) <= 2:
        raise ValueError(
            "each case must have one attempt, plus exactly one schema retry when needed"
        )
    if attempts[0].schema_valid and len(attempts) == 2:
        raise ValueError("schema-valid semantic predictions must not be retried")
    if not attempts[0].schema_valid and len(attempts) != 2:
        raise ValueError("a schema-invalid first attempt requires exactly one retry")


def _summarize_performance(results: Sequence[CaseResult]) -> BenchmarkPerformanceSummary:
    latencies = sorted(result.latency_ms for result in results if result.latency_ms is not None)
    return BenchmarkPerformanceSummary(
        warm_sample_count=len(latencies),
        warm_p50_ms=_percentile(latencies, 0.50),
        warm_p95_ms=_percentile(latencies, 0.95),
        input_tokens=_optional_sum(result.input_tokens for result in results),
        output_tokens=_optional_sum(result.output_tokens for result in results),
        total_tokens=_optional_sum(result.total_tokens for result in results),
    )


def _summarize(
    rows: Sequence[tuple[GoldCase, list[AttemptValidation], CaseResult]],
) -> BenchmarkSummary:
    count = len(rows)
    gold_execute = Counter()
    pred_execute = Counter()
    true_execute = Counter()
    gold_handoff = Counter()
    pred_handoff = Counter()
    true_handoff = Counter()
    forbidden = 0
    unsupported = 0
    for case, attempts, result in rows:
        case_gold_execute = _execute_counter(case.gold_route_plan)
        case_pred_execute = _execute_counter(result.prediction)
        case_gold_handoff = _handoff_counter(case.gold_route_plan)
        case_pred_handoff = _handoff_counter(result.prediction)
        gold_execute += case_gold_execute
        pred_execute += case_pred_execute
        true_execute += case_gold_execute & case_pred_execute
        gold_handoff += case_gold_handoff
        pred_handoff += case_pred_handoff
        true_handoff += case_gold_handoff & case_pred_handoff
        for attempt in attempts:
            for name in attempt.requested_capability_names:
                forbidden += int(name in FORBIDDEN_EXECUTE)
                unsupported += int(name not in ALLOWED_EXECUTE)
            unsupported += sum(
                target not in ALLOWED_HANDOFFS for target in attempt.handoff_target_names
            )

    multi_rows = [row for row in rows if sum(_execute_counter(row[0].gold_route_plan).values()) > 1]
    mixed_rows = [
        row for row in rows if row[0].gold_route_plan.requests and row[0].gold_route_plan.handoffs
    ]
    clarify_tp = sum(row[2].clarify_gold and row[2].clarify_predicted for row in rows)
    clarify_pred = sum(row[2].clarify_predicted for row in rows)
    clarify_gold_count = sum(row[2].clarify_gold for row in rows)
    clarify_fp = sum(not row[2].clarify_gold and row[2].clarify_predicted for row in rows)
    non_clarify_gold = count - clarify_gold_count

    return BenchmarkSummary(
        case_count=count,
        first_pass_schema_valid_rate=_ratio(
            sum(r.first_pass_schema_valid for _, _, r in rows), count
        ),
        retry_recovery_count=sum(
            not attempts[0].schema_valid and result.final_schema_valid
            for _, attempts, result in rows
        ),
        final_schema_valid_rate=_ratio(sum(r.final_schema_valid for _, _, r in rows), count),
        unrecovered_schema_failure_count=sum(not r.final_schema_valid for _, _, r in rows),
        exact_route_plan_match=_ratio(sum(r.exact_match for _, _, r in rows), count),
        executable_precision=_ratio(
            sum(true_execute.values()), sum(pred_execute.values()), empty=1.0
        ),
        executable_recall=_ratio(sum(true_execute.values()), sum(gold_execute.values()), empty=1.0),
        training_precision=_ratio(true_execute["training"], pred_execute["training"], empty=1.0),
        training_recall=_ratio(true_execute["training"], gold_execute["training"], empty=1.0),
        life_precision=_ratio(true_execute["life"], pred_execute["life"], empty=1.0),
        life_recall=_ratio(true_execute["life"], gold_execute["life"], empty=1.0),
        walk_precision=_ratio(true_execute["walk"], pred_execute["walk"], empty=1.0),
        walk_recall=_ratio(true_execute["walk"], gold_execute["walk"], empty=1.0),
        multi_execute_recall=_restricted_execute_recall(multi_rows),
        exact_executable_set_accuracy_multi=_ratio(
            sum(
                _execute_counter(case.gold_route_plan) == _execute_counter(result.prediction)
                for case, _, result in multi_rows
            ),
            len(multi_rows),
        ),
        handoff_precision=_ratio(sum(true_handoff.values()), sum(pred_handoff.values()), empty=1.0),
        handoff_recall=_ratio(sum(true_handoff.values()), sum(gold_handoff.values()), empty=1.0),
        skin_handoff_recall=_ratio(true_handoff["skin"], gold_handoff["skin"], empty=1.0),
        gait_handoff_recall=_ratio(true_handoff["gait"], gold_handoff["gait"], empty=1.0),
        exact_mixed_execute_handoff_match=_ratio(
            sum(result.exact_match for _, _, result in mixed_rows), len(mixed_rows)
        ),
        clarify_precision=_ratio(clarify_tp, clarify_pred, empty=1.0),
        clarify_recall=_ratio(clarify_tp, clarify_gold_count, empty=1.0),
        false_positive_clarify_rate=_ratio(clarify_fp, non_clarify_gold, empty=0.0),
        forbidden_execute_count=forbidden,
        invented_unsupported_capability_count=unsupported,
    )


def _semantic_plan_key(plan: RoutePlan | None) -> tuple[Any, ...] | None:
    """Canonical routing meaning; normalize observation and user-facing wording.

    router/model are observation metadata. Handoff reason and clarify question are presentation text;
    routing semantics are the request+payload multiset, handoff target multiset, and missing-key set.
    """
    if plan is None:
        return None
    requests = sorted(
        (
            request.capability.value,
            json.dumps(request.payload.model_dump(mode="json"), ensure_ascii=False, sort_keys=True),
            request.timeout_ms,
        )
        for request in plan.requests
    )
    handoffs = sorted(handoff.target for handoff in plan.handoffs)
    clarify = None if plan.clarify is None else tuple(sorted(plan.clarify.missing))
    return tuple(requests), tuple(handoffs), clarify


def _raw_capability_names(raw: object) -> list[str]:
    if isinstance(raw, RoutePlan):
        return [request.capability.value for request in raw.requests]
    if not isinstance(raw, Mapping):
        return []
    requests = raw.get("requests")
    if not isinstance(requests, list):
        return []
    return [
        str(item["capability"])
        for item in requests
        if isinstance(item, Mapping) and isinstance(item.get("capability"), str)
    ]


def _raw_handoff_names(raw: object) -> list[str]:
    if isinstance(raw, RoutePlan):
        return [handoff.target for handoff in raw.handoffs]
    if not isinstance(raw, Mapping):
        return []
    handoffs = raw.get("handoffs")
    if not isinstance(handoffs, list):
        return []
    return [
        str(item["target"])
        for item in handoffs
        if isinstance(item, Mapping) and isinstance(item.get("target"), str)
    ]


def _execute_counter(plan: RoutePlan | None) -> Counter[str]:
    if plan is None:
        return Counter()
    return Counter(request.capability.value for request in plan.requests)


def _handoff_counter(plan: RoutePlan | None) -> Counter[str]:
    if plan is None:
        return Counter()
    return Counter(handoff.target for handoff in plan.handoffs)


def _true_positives(gold: Counter[str], predicted: Counter[str]) -> int:
    return sum((gold & predicted).values())


def _precision(gold: Counter[str], predicted: Counter[str]) -> float:
    return _ratio(_true_positives(gold, predicted), sum(predicted.values()), empty=1.0)


def _recall(gold: Counter[str], predicted: Counter[str]) -> float:
    return _ratio(_true_positives(gold, predicted), sum(gold.values()), empty=1.0)


def _restricted_execute_recall(
    rows: Sequence[tuple[GoldCase, list[AttemptValidation], CaseResult]],
) -> float:
    gold_total = 0
    true_total = 0
    for case, _, result in rows:
        gold = _execute_counter(case.gold_route_plan)
        predicted = _execute_counter(result.prediction)
        gold_total += sum(gold.values())
        true_total += sum((gold & predicted).values())
    return _ratio(true_total, gold_total, empty=1.0)


def _ratio(numerator: int, denominator: int, *, empty: float = 0.0) -> float:
    return numerator / denominator if denominator else empty


def _percentile(values: Sequence[float], quantile: float) -> float | None:
    """Linear interpolation over warm observations; None means Phase 2 data is absent."""
    if not values:
        return None
    position = (len(values) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return values[lower] + (values[upper] - values[lower]) * fraction


def _optional_sum(values: Iterable[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return sum(present) if present else None
