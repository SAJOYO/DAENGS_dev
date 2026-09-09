"""One-shot Gemini runner for Card 2A semantic-only router v2 remediation."""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable, Sequence
from typing import Any

from daengs_evals import EVALS_DIR

from .prompt import MODEL_ID
from .prompt_v2 import PROMPT_VERSION, build_semantic_router_prompt
from .runner import FREEZE_COMMIT, build_artifacts, create_gemini_client, write_artifacts
from .schemas import AttemptValidation, GoldCase, PerformanceObservation, load_gold_cases
from .semantic_v2 import SemanticRoutingDecision, assemble_route_plan, validate_semantic_decision

GENERATION_CONFIG = {
    "temperature": 0.0,
    "candidate_count": 1,
    "max_output_tokens": 256,
    "response_mime_type": "application/json",
    "response_json_schema": "SemanticRoutingDecision.model_json_schema()",
}
RESULTS_DIR = EVALS_DIR / "orchestration_router"
RESULTS_PATH = RESULTS_DIR / "results_v2.jsonl"
SUMMARY_PATH = RESULTS_DIR / "summary_v2.json"
REPORT_PATH = RESULTS_DIR / "phase2_v2_report.md"


def _provider_config() -> Any:
    from google.genai import types

    return types.GenerateContentConfig(
        temperature=GENERATION_CONFIG["temperature"],
        candidate_count=GENERATION_CONFIG["candidate_count"],
        max_output_tokens=GENERATION_CONFIG["max_output_tokens"],
        response_mime_type=GENERATION_CONFIG["response_mime_type"],
        response_json_schema=SemanticRoutingDecision.model_json_schema(),
    )


def run_cases(
    cases: Sequence[GoldCase],
    *,
    client: Any,
    prompt_builder: Callable[..., str] = build_semantic_router_prompt,
    model_id: str = MODEL_ID,
) -> tuple[dict[str, list[AttemptValidation]], dict[str, PerformanceObservation]]:
    """Run sequentially; retry only an invalid SemanticRoutingDecision."""
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
                model=model_id,
                contents=prompt_builder(query=case.query, context=case.context),
                config=config,
            )
            elapsed_ms += (time.perf_counter() - started) * 1000
            semantic = validate_semantic_decision(_response_value(response))
            plan = (
                assemble_route_plan(
                    semantic.decision,
                    query=case.query,
                    context=case.context,
                    model_id=model_id,
                )
                if semantic.decision is not None
                else None
            )
            validation = AttemptValidation(
                schema_valid=semantic.schema_valid,
                plan=plan,
                requested_capability_names=semantic.execute_names,
                handoff_target_names=semantic.handoff_names,
                error_category=semantic.error_category,
            )
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
    return parsed if parsed is not None else getattr(response, "text", None)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", required=True)
    parser.parse_args()
    cases = load_gold_cases()
    attempts, performance = run_cases(cases, client=create_gemini_client())
    records, summary, report = build_artifacts(
        cases,
        attempts,
        performance,
        prompt_version=PROMPT_VERSION,
        generation_config=GENERATION_CONFIG,
    )
    write_artifacts(
        records,
        summary,
        report,
        results_path=RESULTS_PATH,
        summary_path=SUMMARY_PATH,
        report_path=REPORT_PATH,
    )
    print(
        f"{summary['verdict']}: {len(cases)} cases, {summary['total_provider_attempts']} attempts"
    )


if __name__ == "__main__":
    main()


__all__ = ["FREEZE_COMMIT", "GENERATION_CONFIG", "run_cases"]
