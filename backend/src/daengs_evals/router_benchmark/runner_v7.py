"""Prompt-regression run: production `semantic-router-ko-v6` against the unchanged v3 gold/gates.

v6 (PR #172) corrects one sentence of v5. v5 had narrowed Life to formal institutional /
legal / administrative / policy / contractual evidence and declared general pet husbandry
unsupported — correct, and kept verbatim — but its "walk or exercise frequency or duration
… not Walk merely because it concerns walking" wording also suppressed Walk for today's /
this evening's walking time-window questions: run v6 lost Walk on frozen `mixed_10` and
`clarify_08` and failed the `exact_mixed_execute_handoff_match` gate (0.80 < 0.90). v6
distinguishes ROUTINE or normative exercise advice (unsupported, empty decision) from
CURRENT-day timing/suitability, including choosing a suitable walking window for today
(Walk), without broadening Walk into recurring care schedules. No new ExecuteName, no
schema change, no model change, no answer generation.

Same 80 corrected v3 gold cases, same frozen gates, same `gemini-3.1-flash-lite`, run
exactly once, driving the PRODUCTION prompt builder and schema. v1–v6 artifacts are not
modified; the v6 FAIL artifacts stay as evidence.
"""

from __future__ import annotations

import argparse

from daengs_backend.orchestration.semantic import PROMPT_VERSION, ROUTER_MODEL_ID
from daengs_evals import EVALS_DIR

from .runner import build_artifacts, create_gemini_client, write_artifacts
from .runner_v2 import GENERATION_CONFIG
from .runner_v5 import run_cases, social_intent_section
from .schemas import load_gold_v3_cases

MODEL_ID = ROUTER_MODEL_ID
BENCHMARK_ID = "orchestration-router-v7"
GOLD_VERSION = "gold-v3-overlay-mixed-09"
RESULTS_DIR = EVALS_DIR / "orchestration_router"
RESULTS_PATH = RESULTS_DIR / "results_v7.jsonl"
SUMMARY_PATH = RESULTS_DIR / "summary_v7.json"
REPORT_PATH = RESULTS_DIR / "phase2_v7_report.md"


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


__all__ = ["BENCHMARK_ID", "GOLD_VERSION", "MODEL_ID"]
