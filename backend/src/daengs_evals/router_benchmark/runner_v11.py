"""Prompt-regression run: production `semantic-router-ko-v10` against the unchanged v3 gold/gates.

v10 narrows **Place** (D-057 ④ follow-up). #277's off-domain stratum leaked 6 of 21 requests
into an answer; a 12-case probe with controls (2026-09-07) showed every leak had the same
shape — the router picked `place` for a venue that was not for a dog (a cat cafe, a cat-food
shop, a human clinic, an Italian restaurant), because v9's Place definition reads the *asking
for a place* rather than *what the place is for*. The safety prompt is untouched: `general`
was already refusing its own share correctly.

No model change, no schema change; the gates, gold and single-schema-retry policy are the
v10 run's, unchanged. The two views (raw / general-stripped) are v10's as well and the code
is imported from `runner_v10` rather than copied — the ONE changed variable versus the
recorded v10 run is the production prompt, and that is what makes the two summaries
comparable. v1–v10 artifacts are not modified.
"""

from __future__ import annotations

import argparse

from daengs_backend.orchestration.semantic import PROMPT_VERSION

from .runner import build_artifacts, create_gemini_client, write_artifacts
from .runner_v2 import GENERATION_CONFIG
from .runner_v5 import social_intent_section
from .runner_v10 import (
    GOLD_VERSION,
    MODEL_ID,
    RESULTS_DIR,
    general_case_ids,
    general_section,
    run_cases,
    strip_general_everywhere,
)
from .schemas import load_gold_v3_cases

BENCHMARK_ID = "orchestration-router-v11"
STRIPPED_BENCHMARK_ID = "orchestration-router-v11-general-stripped"

RESULTS_PATH = RESULTS_DIR / "results_v11.jsonl"
SUMMARY_PATH = RESULTS_DIR / "summary_v11.json"
REPORT_PATH = RESULTS_DIR / "phase2_v11_report.md"


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
