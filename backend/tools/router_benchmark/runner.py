"""Single-purpose Gemini runner for the frozen Card 2A acceptance benchmark."""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from daengs_backend.orchestration.contracts import RoutePlan

from .evaluate import apply_acceptance_gates, evaluate_benchmark, validate_prediction
from .prompt import MODEL_ID, PROMPT_VERSION, build_router_prompt
from .schemas import (
    AttemptValidation,
    GoldCase,
    PerformanceObservation,
    load_benchmark_config,
    load_gold_cases,
)

FREEZE_COMMIT = "204b12fa774ebcc324eb55863d0f39dbcefa892b"
GENERATION_CONFIG = {
    "temperature": 0.0,
    "candidate_count": 1,
    "max_output_tokens": 1024,
    "response_mime_type": "application/json",
    "response_json_schema": "RoutePlan.model_json_schema()",
}
RESULTS_DIR = Path(__file__).resolve().parents[2] / "evals" / "orchestration_router"
RESULTS_PATH = RESULTS_DIR / "results_v1.jsonl"
SUMMARY_PATH = RESULTS_DIR / "summary_v1.json"
REPORT_PATH = RESULTS_DIR / "phase2_report.md"


def _provider_config() -> Any:
    from google.genai import types

    return types.GenerateContentConfig(
        temperature=GENERATION_CONFIG["temperature"],
        candidate_count=GENERATION_CONFIG["candidate_count"],
        max_output_tokens=GENERATION_CONFIG["max_output_tokens"],
        response_mime_type=GENERATION_CONFIG["response_mime_type"],
        response_json_schema=RoutePlan.model_json_schema(),
    )


def create_gemini_client() -> Any:
    """Create the existing Google client without exposing its credential."""
    from google import genai
    from google.genai import types

    from daengs_life.rag.core.config import settings

    api_key = settings.gemini_api_key.strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required in the existing backend environment")
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=settings.gemini_timeout_ms),
    )


def run_cases(
    cases: Sequence[GoldCase],
    *,
    client: Any,
) -> tuple[dict[str, list[AttemptValidation]], dict[str, PerformanceObservation]]:
    """Call Gemini sequentially and retry only a schema-invalid first response."""
    attempts_by_case: dict[str, list[AttemptValidation]] = {}
    performance_by_case: dict[str, PerformanceObservation] = {}
    config = _provider_config()

    for case in cases:
        attempts: list[AttemptValidation] = []
        elapsed_ms = 0.0
        input_tokens = output_tokens = total_tokens = 0
        tokens_available = True

        for _attempt_number in range(2):
            started = time.perf_counter()
            response = client.models.generate_content(
                model=MODEL_ID,
                contents=build_router_prompt(query=case.query, context=case.context),
                config=config,
            )
            elapsed_ms += (time.perf_counter() - started) * 1000
            validation = validate_prediction(_response_value(response))
            attempts.append(validation)
            usage = getattr(response, "usage_metadata", None)
            if usage is None:
                tokens_available = False
            else:
                input_tokens += int(getattr(usage, "prompt_token_count", 0) or 0)
                output_tokens += int(getattr(usage, "candidates_token_count", 0) or 0)
                total_tokens += int(getattr(usage, "total_token_count", 0) or 0)
            if validation.schema_valid:
                break

        attempts_by_case[case.case_id] = attempts
        performance_by_case[case.case_id] = PerformanceObservation(
            latency_ms=elapsed_ms,
            input_tokens=input_tokens if tokens_available else None,
            output_tokens=output_tokens if tokens_available else None,
            total_tokens=total_tokens if tokens_available else None,
        )

    return attempts_by_case, performance_by_case


def _response_value(response: Any) -> object:
    parsed = getattr(response, "parsed", None)
    if parsed is not None:
        return parsed
    return getattr(response, "text", None)


def classify_failure(case: GoldCase, prediction: RoutePlan | None) -> str | None:
    """Assign one compact deterministic bucket to a non-exact final prediction."""
    if prediction is None:
        return "SCHEMA_FAILURE"
    gold = case.gold_route_plan
    if gold.clarify is not None and prediction.clarify is None:
        return "MISSED_CLARIFY"
    if gold.clarify is None and prediction.clarify is not None:
        return "WRONG_CLARIFY"

    gold_execute = Counter(request.capability.value for request in gold.requests)
    predicted_execute = Counter(request.capability.value for request in prediction.requests)
    gold_handoffs = Counter(handoff.target for handoff in gold.handoffs)
    predicted_handoffs = Counter(handoff.target for handoff in prediction.handoffs)
    if sum(gold_execute.values()) > 1 and sum(predicted_execute.values()) < sum(
        gold_execute.values()
    ):
        return "MULTI_INTENT_COLLAPSE"
    if gold_execute - predicted_execute:
        return "MISSED_EXECUTE"
    if predicted_execute - gold_execute:
        return "EXTRA_EXECUTE"
    if gold_handoffs - predicted_handoffs:
        return "MISSED_HANDOFF"
    if predicted_handoffs - gold_handoffs:
        return "EXTRA_HANDOFF"
    return "PAYLOAD_MISMATCH"


def build_artifacts(
    cases: Sequence[GoldCase],
    attempts_by_case: dict[str, list[AttemptValidation]],
    performance_by_case: dict[str, PerformanceObservation],
    *,
    prompt_version: str = PROMPT_VERSION,
    generation_config: dict[str, Any] = GENERATION_CONFIG,
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    """Reuse the frozen evaluator and shape its output into auditable artifacts."""
    config = load_benchmark_config()
    evaluation = evaluate_benchmark(
        cases,
        attempts_by_case,
        performance_by_case,
        prompt_version=prompt_version,
    )
    verdict = apply_acceptance_gates(evaluation.summary, config)
    by_id = {case.case_id: case for case in cases}
    records: list[dict[str, Any]] = []
    failure_counts: Counter[str] = Counter()

    for result in evaluation.results:
        case = by_id[result.case_id]
        bucket = None if result.exact_match else classify_failure(case, result.prediction)
        if bucket:
            failure_counts[bucket] += 1
        attempts = attempts_by_case[result.case_id]
        record = {
            "benchmark_id": config.benchmark_id,
            "freeze_commit": FREEZE_COMMIT,
            "category": case.category,
            **result.model_dump(mode="json"),
            "failure_bucket": bucket,
            "attempt_errors": [attempt.error_category for attempt in attempts],
        }
        records.append(record)

    total_attempts = sum(len(attempts) for attempts in attempts_by_case.values())
    summary = {
        "benchmark_id": config.benchmark_id,
        "freeze_commit": FREEZE_COMMIT,
        "model": MODEL_ID,
        "prompt_version": prompt_version,
        "generation_config": generation_config,
        "scored_cases": len(cases),
        "total_provider_attempts": total_attempts,
        "retry_count": total_attempts - len(cases),
        "metrics": evaluation.summary.model_dump(mode="json"),
        "performance": evaluation.performance.model_dump(mode="json"),
        "gate_checks": [check.model_dump(mode="json") for check in verdict.checks],
        "verdict": verdict.verdict,
        "failing_case_ids": [record["case_id"] for record in records if not record["exact_match"]],
        "failure_categories": dict(sorted(failure_counts.items())),
    }
    return records, summary, _markdown_report(summary)


def write_artifacts(
    records: list[dict[str, Any]],
    summary: dict[str, Any],
    report: str,
    *,
    results_path: Path = RESULTS_PATH,
    summary_path: Path = SUMMARY_PATH,
    report_path: Path = REPORT_PATH,
) -> None:
    results_path.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report_path.write_text(report, encoding="utf-8")


def _markdown_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Card 2A Phase 2 acceptance result",
        "",
        f"- Verdict: **{summary['verdict']}**",
        f"- Model: `{summary['model']}`",
        f"- Prompt: `{summary['prompt_version']}`",
        f"- Freeze: `{summary['freeze_commit']}`",
        (
            f"- Cases / attempts / retries: {summary['scored_cases']} / "
            f"{summary['total_provider_attempts']} / {summary['retry_count']}"
        ),
        "",
        "## Frozen gate checks",
        "",
        "| Metric | Actual | Gate | Pass |",
        "| --- | ---: | ---: | :---: |",
    ]
    for check in summary["gate_checks"]:
        lines.append(
            f"| `{check['metric']}` | {check['actual']:.6g} | "
            f"{check['operator']} {check['threshold']:.6g} | "
            f"{'yes' if check['passed'] else 'no'} |"
        )
    lines.extend(
        [
            "",
            "## Failures",
            "",
            f"- Case IDs: {', '.join(summary['failing_case_ids']) or 'none'}",
            f"- Buckets: {json.dumps(summary['failure_categories'], ensure_ascii=False, sort_keys=True)}",
            "",
            "Latency and token measurements are observational only.",
        ]
    )
    return "\n".join(lines) + "\n"


def smoke_check(case_id: str, *, client: Any) -> AttemptValidation:
    cases = {case.case_id: case for case in load_gold_cases()}
    if case_id not in cases:
        raise ValueError(f"unknown frozen case: {case_id}")
    attempts, _ = run_cases([cases[case_id]], client=client)
    result = attempts[case_id][-1]
    if not result.schema_valid:
        raise RuntimeError("Gemini structured output did not validate as RoutePlan")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--smoke", metavar="CASE_ID")
    mode.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    client = create_gemini_client()
    if args.smoke:
        smoke_check(args.smoke, client=client)
        print(f"structured smoke check passed: {args.smoke}")
        return

    cases = load_gold_cases()
    attempts, performance = run_cases(cases, client=client)
    records, summary, report = build_artifacts(cases, attempts, performance)
    write_artifacts(records, summary, report)
    print(
        f"{summary['verdict']}: {len(cases)} cases, {summary['total_provider_attempts']} attempts"
    )


if __name__ == "__main__":
    main()
