"""Prompt-regression run: production `semantic-router-ko-v4` against the unchanged v3 gold/gates.

v4 adds one narrow classification to the production router (`social_intent`:
greeting / thanks / goodbye, exclusive with execute/handoffs) so that pure small
talk gets a deterministic template instead of a capability failure. Because the
production prompt and schema changed, the accepted v1 routing behavior must be
re-checked: the SAME 80 corrected v3 gold cases, the SAME frozen acceptance gates,
the SAME model (`gemini-3.1-flash-lite`), run exactly once. None of the 80 cases is
social small talk, so the expected `social_intent` is null on every case; the report
records how many cases came back non-null.

Unlike runner_v2..v4 this runner drives the PRODUCTION prompt builder and schema
(`daengs_backend.orchestration.semantic` / `planner`) rather than the frozen
benchmark copies, because the point is to certify what production will actually
send. v1-v4 artifacts are not modified.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from daengs_backend.orchestration.contracts import RouterKind
from daengs_backend.orchestration.planner import assemble_route_plan
from daengs_backend.orchestration.semantic import (
    PROMPT_VERSION,
    ROUTER_MODEL_ID,
    SemanticRoutingDecision,
    build_semantic_router_prompt,
    validate_semantic_decision,
)

from .runner import build_artifacts, create_gemini_client, write_artifacts
from .runner_v2 import GENERATION_CONFIG
from .runner_v2 import _provider_config as _v2_provider_config
from .schemas import AttemptValidation, GoldCase, PerformanceObservation, load_gold_v3_cases

MODEL_ID = ROUTER_MODEL_ID
BENCHMARK_ID = "orchestration-router-v5"
GOLD_VERSION = "gold-v3-overlay-mixed-09"
RESULTS_DIR = Path(__file__).resolve().parents[2] / "evals" / "orchestration_router"
RESULTS_PATH = RESULTS_DIR / "results_v5.jsonl"
SUMMARY_PATH = RESULTS_DIR / "summary_v5.json"
REPORT_PATH = RESULTS_DIR / "phase2_v5_report.md"


def _provider_config() -> Any:
    # Same frozen generation config as v2..v4; only the JSON schema is the production one.
    return _v2_provider_config().model_copy(
        update={"response_json_schema": SemanticRoutingDecision.model_json_schema()}
    )


def _raw_names(raw: object, key: str) -> list[str]:
    if isinstance(raw, SemanticRoutingDecision):
        return list(getattr(raw, key))
    if not isinstance(raw, Mapping) or not isinstance(raw.get(key), list):
        return []
    return [str(item) for item in raw[key] if isinstance(item, str)]


def _response_value(response: Any) -> object:
    parsed = getattr(response, "parsed", None)
    return parsed if parsed is not None else getattr(response, "text", None)


def run_cases(
    cases: Sequence[GoldCase],
    *,
    client: Any,
    model_id: str = MODEL_ID,
) -> tuple[
    dict[str, list[AttemptValidation]],
    dict[str, PerformanceObservation],
    dict[str, str | None],
]:
    """Run sequentially with the production prompt/schema; retry only a schema failure.

    Returns the same attempt/performance maps as runner_v2.run_cases plus the final
    `social_intent` per case (expected null everywhere in the 80-case gold set).
    """
    attempts_by_case: dict[str, list[AttemptValidation]] = {}
    performance_by_case: dict[str, PerformanceObservation] = {}
    social_by_case: dict[str, str | None] = {}
    config = _provider_config()

    for case in cases:
        attempts: list[AttemptValidation] = []
        elapsed_ms = 0.0
        input_tokens = output_tokens = total_tokens = 0
        tokens_available = True
        social_by_case[case.case_id] = None

        for _attempt_number in range(2):
            started = time.perf_counter()
            response = client.models.generate_content(
                model=model_id,
                contents=build_semantic_router_prompt(query=case.query, context=case.context),
                config=config,
            )
            elapsed_ms += (time.perf_counter() - started) * 1000
            raw = _response_value(response)
            decision = validate_semantic_decision(raw)
            if decision is None:
                validation = AttemptValidation(
                    schema_valid=False,
                    requested_capability_names=_raw_names(raw, "execute"),
                    handoff_target_names=_raw_names(raw, "handoffs"),
                    error_category="schema_validation_error",
                )
            else:
                validation = AttemptValidation(
                    schema_valid=True,
                    plan=assemble_route_plan(
                        decision,
                        query=case.query,
                        context=case.context,
                        router=RouterKind.LLM,
                        model=model_id,
                    ),
                    requested_capability_names=list(decision.execute),
                    handoff_target_names=list(decision.handoffs),
                )
                social_by_case[case.case_id] = decision.social_intent
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

    return attempts_by_case, performance_by_case, social_by_case


def social_intent_section(social_by_case: Mapping[str, str | None]) -> str:
    flagged = sorted(case_id for case_id, intent in social_by_case.items() if intent is not None)
    lines = [
        "",
        "## social_intent (v4 addition)",
        "",
        "None of the 80 gold cases is pure small talk, so every case is expected to leave",
        "`social_intent` null; capability intent must take precedence over social wording.",
        "",
        f"- Non-null `social_intent` cases: {len(flagged)} / {len(social_by_case)}",
    ]
    if flagged:
        lines.append(f"- Case IDs: {', '.join(flagged)}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", required=True)
    parser.parse_args()
    cases = load_gold_v3_cases()
    attempts, performance, social = run_cases(cases, client=create_gemini_client())
    records, summary, report = build_artifacts(
        cases,
        attempts,
        performance,
        prompt_version=PROMPT_VERSION,
        generation_config=GENERATION_CONFIG,
        benchmark_id=BENCHMARK_ID,
        gold_version=GOLD_VERSION,
        model_id=MODEL_ID,
    )
    summary["social_intent_non_null_case_ids"] = sorted(
        case_id for case_id, intent in social.items() if intent is not None
    )
    write_artifacts(
        records,
        summary,
        report + social_intent_section(social),
        results_path=RESULTS_PATH,
        summary_path=SUMMARY_PATH,
        report_path=REPORT_PATH,
    )
    print(
        f"{summary['verdict']}: {len(cases)} cases, {summary['total_provider_attempts']} attempts,"
        f" social_intent non-null: {len(summary['social_intent_non_null_case_ids'])}"
    )


if __name__ == "__main__":
    main()


__all__ = ["BENCHMARK_ID", "GOLD_VERSION", "MODEL_ID", "run_cases", "social_intent_section"]
