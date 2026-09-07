"""Prompt-regression run: production `semantic-router-ko-v9` against the unchanged v3 gold/gates.

v9 (D-057 ①) makes `general` a router destination — additive, never a replacement: a care
or health worry mixed into a weather/venue/institution utterance keeps its care part, and
a request that is not about dogs at all selects nothing. No model change; the schema gains
one enum value; the gates, gold and single-schema-retry policy are unchanged.

**Two views, one paid run.** The frozen gold predates the fallback and has no `general`
anywhere, so every `general` the router now adds is, by that gold, a precision miss —
while by D-057 it is the intended policy. Reporting one number would hide one of those two
facts. So:

- **raw** — the plan the planner builds with the fallback ON (`general_fallback=True`),
  i.e. what production would build once the switch is thrown. `general` counts.
- **general-stripped** — the same attempts with every `general` request removed before
  scoring. Because the planner with the flag OFF strips `general` from the decision, this
  is byte-for-byte the plan production builds today, and it is the view that must not
  regress: it has to match or beat v9 (exact ≥ 0.975, all 15 gates PASS). If it does not,
  D-057 ③ says report it — do not tune.

The raw view's own gates are informational (the recorded `verdict` is the stripped one's
twin, kept separately under `general_stripped`). v1–v9 artifacts are not modified.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from daengs_backend.orchestration.contracts import CapabilityName, RouterKind
from daengs_backend.orchestration.planner import assemble_route_plan
from daengs_backend.orchestration.semantic import (
    PROMPT_VERSION,
    ROUTER_MODEL_ID,
    build_semantic_router_prompt,
    validate_semantic_decision,
)

from .runner import build_artifacts, create_gemini_client, write_artifacts
from .runner_v2 import GENERATION_CONFIG
from .runner_v5 import _provider_config, _raw_names, _response_value, social_intent_section
from .schemas import AttemptValidation, GoldCase, PerformanceObservation, load_gold_v3_cases

MODEL_ID = ROUTER_MODEL_ID
BENCHMARK_ID = "orchestration-router-v10"
STRIPPED_BENCHMARK_ID = "orchestration-router-v10-general-stripped"
GOLD_VERSION = "gold-v3-overlay-mixed-09"
RESULTS_DIR = Path(__file__).resolve().parents[2] / "evals" / "orchestration_router"
RESULTS_PATH = RESULTS_DIR / "results_v10.jsonl"
SUMMARY_PATH = RESULTS_DIR / "summary_v10.json"
REPORT_PATH = RESULTS_DIR / "phase2_v10_report.md"


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
    """`runner_v5.run_cases` with the fallback ON in the planner, so `general` survives.

    Everything else is identical: production prompt and schema, sequential, one schema
    retry. The flag-on assembly is what makes the raw view observable at all — with the
    default (flag off) the planner would strip `general` before it could be counted.
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
                        general_fallback=True,
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


def strip_general(attempt: AttemptValidation) -> AttemptValidation:
    """The same attempt as production builds it with the flag off: no `general` request."""
    names = [name for name in attempt.requested_capability_names if name != "general"]
    if attempt.plan is None:
        return attempt.model_copy(update={"requested_capability_names": names})
    plan = attempt.plan.model_copy(
        update={
            "requests": [
                request
                for request in attempt.plan.requests
                if request.capability != CapabilityName.GENERAL
            ]
        }
    )
    return attempt.model_copy(update={"plan": plan, "requested_capability_names": names})


def strip_general_everywhere(
    attempts_by_case: dict[str, list[AttemptValidation]],
) -> dict[str, list[AttemptValidation]]:
    return {
        case_id: [strip_general(attempt) for attempt in attempts]
        for case_id, attempts in attempts_by_case.items()
    }


def general_case_ids(attempts_by_case: dict[str, list[AttemptValidation]]) -> list[str]:
    return sorted(
        case_id
        for case_id, attempts in attempts_by_case.items()
        if "general" in attempts[-1].requested_capability_names
    )


def general_section(summary: dict[str, Any], stripped: dict[str, Any], selected: list[str]) -> str:
    lines = [
        "",
        "## `general` — two views (D-057 ⑤)",
        "",
        "The frozen gold has no `general`, so every `general` the v9 router adds is a precision",
        "miss by that gold and the intended policy by D-057. Both views are scored from the ONE",
        "paid run above; the stripped view is what production builds with the flag off.",
        "",
        f"- Cases where the router selected `general`: {len(selected)} / {summary['scored_cases']}",
        f"- Case IDs: {', '.join(selected) or 'none'}",
        "",
        "| View | Verdict | exact match | executable precision | executable recall |",
        "| --- | :---: | ---: | ---: | ---: |",
    ]
    for label, view in (("raw (general counted)", summary), ("general-stripped", stripped)):
        metrics = view["metrics"]
        lines.append(
            f"| {label} | {view['verdict']} | {metrics['exact_route_plan_match']:.6g} | "
            f"{metrics['executable_precision']:.6g} | {metrics['executable_recall']:.6g} |"
        )
    lines.extend(
        [
            "",
            "### general-stripped gate checks",
            "",
            "| Metric | Actual | Gate | Pass |",
            "| --- | ---: | ---: | :---: |",
        ]
    )
    for check in stripped["gate_checks"]:
        lines.append(
            f"| `{check['metric']}` | {check['actual']:.6g} | "
            f"{check['operator']} {check['threshold']:.6g} | "
            f"{'yes' if check['passed'] else 'no'} |"
        )
    lines.extend(
        [
            "",
            f"- general-stripped failures: {', '.join(stripped['failing_case_ids']) or 'none'}",
        ]
    )
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
    _, stripped_summary, _ = build_artifacts(
        cases,
        strip_general_everywhere(attempts),
        performance,
        prompt_version=PROMPT_VERSION,
        generation_config=GENERATION_CONFIG,
        benchmark_id=STRIPPED_BENCHMARK_ID,
        gold_version=GOLD_VERSION,
        model_id=MODEL_ID,
    )
    selected = general_case_ids(attempts)
    summary["social_intent_non_null_case_ids"] = sorted(
        case_id for case_id, intent in social.items() if intent is not None
    )
    summary["general_selected_case_ids"] = selected
    summary["general_stripped"] = {
        key: stripped_summary[key]
        for key in (
            "benchmark_id",
            "metrics",
            "gate_checks",
            "verdict",
            "failing_case_ids",
            "failure_categories",
        )
    }
    write_artifacts(
        records,
        summary,
        report
        + social_intent_section(social)
        + general_section(summary, stripped_summary, selected),
        results_path=RESULTS_PATH,
        summary_path=SUMMARY_PATH,
        report_path=REPORT_PATH,
    )
    print(
        f"raw {summary['verdict']} / stripped {stripped_summary['verdict']}: {len(cases)} cases,"
        f" {summary['total_provider_attempts']} attempts, general selected: {len(selected)},"
        f" social_intent non-null: {len(summary['social_intent_non_null_case_ids'])},"
        f" total_tokens: {summary['performance']['total_tokens']}"
    )


if __name__ == "__main__":
    main()


__all__ = [
    "BENCHMARK_ID",
    "GOLD_VERSION",
    "MODEL_ID",
    "STRIPPED_BENCHMARK_ID",
    "general_case_ids",
    "run_cases",
    "strip_general",
    "strip_general_everywhere",
]
