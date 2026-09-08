"""One-shot final Card 2A run using corrected gold and semantic-router-ko-v3."""

from __future__ import annotations

import argparse

from daengs_evals import EVALS_DIR

from .prompt_v3 import PROMPT_VERSION, build_semantic_router_prompt
from .runner import build_artifacts, create_gemini_client, write_artifacts
from .runner_v2 import GENERATION_CONFIG, run_cases
from .schemas import load_gold_v3_cases

RESULTS_DIR = EVALS_DIR / "orchestration_router"
RESULTS_PATH = RESULTS_DIR / "results_v3.jsonl"
SUMMARY_PATH = RESULTS_DIR / "summary_v3.json"
REPORT_PATH = RESULTS_DIR / "phase2_v3_report.md"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", required=True)
    parser.parse_args()
    cases = load_gold_v3_cases()
    attempts, performance = run_cases(
        cases,
        client=create_gemini_client(),
        prompt_builder=build_semantic_router_prompt,
    )
    records, summary, report = build_artifacts(
        cases,
        attempts,
        performance,
        prompt_version=PROMPT_VERSION,
        generation_config=GENERATION_CONFIG,
        benchmark_id="orchestration-router-v3",
        gold_version="gold-v3-overlay-mixed-09",
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
