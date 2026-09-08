"""Prompt-regression run: production `semantic-router-ko-v8` against the unchanged v3 gold/gates.

v8 (PR #279) adds ONE sentence to the router policy and nothing else: an explicit
natural-language exclusion ("this, not that" / "only this") removes the excluded
destination even when its vocabulary is present. v7 already said negation overrides
incidental vocabulary, yet the frozen `boundary_05`-shaped exclusion still gained Walk in
every recorded run (v5 · v6 · v7 · v8 regressions and 3/3 in the #272 comparison). No
model change, no schema change, no new destination.

**What this run does NOT measure is as important as what it does.** #279 also adds the
general-answer fallback, but that is a *planner rule behind a feature flag*, not a router
destination: `assemble_route_plan(..., general_fallback=False)` is the default and this
runner never passes anything else, so every plan scored here is the router's own decision
— exactly what v1–v8 scored. The `general` capability therefore cannot appear in these 80
plans; `evaluate.ALLOWED_EXECUTE` admits it only so that a future flag-on plan scores as a
precision miss instead of an invented capability. The gold file is untouched.

The hope is that `boundary_05` finally lands on Training alone; the risk is that the new
sentence over-reads an ordinary contrast as an exclusion and drops a genuinely requested
destination somewhere in the multi-intent rows (`multi_*`, `mixed_*`). Watch
`executable_recall` (gate 0.95, 100% on every accepted run so far) and
`multi_execute_recall` — those are the numbers that would say the sentence cost more than
it bought. Same 80 corrected v3 gold cases, same frozen gates, same
`gemini-3.1-flash-lite`, run exactly once. v1–v8 artifacts are not modified.
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
BENCHMARK_ID = "orchestration-router-v9"
GOLD_VERSION = "gold-v3-overlay-mixed-09"
RESULTS_DIR = EVALS_DIR / "orchestration_router"
RESULTS_PATH = RESULTS_DIR / "results_v9.jsonl"
SUMMARY_PATH = RESULTS_DIR / "summary_v9.json"
REPORT_PATH = RESULTS_DIR / "phase2_v9_report.md"


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
        f" social_intent non-null: {len(summary['social_intent_non_null_case_ids'])},"
        f" total_tokens: {summary['performance']['total_tokens']}"
    )


if __name__ == "__main__":
    main()


__all__ = ["BENCHMARK_ID", "GOLD_VERSION", "MODEL_ID"]
