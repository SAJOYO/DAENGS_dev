"""Model-substitution acceptance run: unchanged v3 gold/prompt/gates, model=gemini-3.1-flash-lite.

Corrects a model-selection mistake (D-041 Card 2A was accepted using
gemini-3.5-flash-lite instead of the team-approved gemini-3.1-flash-lite). The
prompt (`semantic-router-ko-v3`), gold set, and acceptance gates are byte-for-byte
the same as the v3 run in runner_v3.py; only MODEL_ID changes. See docs/decisions.md.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .prompt_v3 import PROMPT_VERSION, build_semantic_router_prompt
from .runner import build_artifacts, create_gemini_client, write_artifacts
from .runner_v2 import GENERATION_CONFIG, run_cases
from .schemas import load_gold_v3_cases

MODEL_ID = "gemini-3.1-flash-lite"
RESULTS_DIR = Path(__file__).resolve().parents[2] / "evals" / "orchestration_router"
RESULTS_PATH = RESULTS_DIR / "results_v4.jsonl"
SUMMARY_PATH = RESULTS_DIR / "summary_v4.json"
REPORT_PATH = RESULTS_DIR / "phase2_v4_report.md"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", required=True)
    parser.parse_args()
    cases = load_gold_v3_cases()
    attempts, performance = run_cases(
        cases,
        client=create_gemini_client(),
        prompt_builder=build_semantic_router_prompt,
        model_id=MODEL_ID,
    )
    records, summary, report = build_artifacts(
        cases,
        attempts,
        performance,
        prompt_version=PROMPT_VERSION,
        generation_config=GENERATION_CONFIG,
        benchmark_id="orchestration-router-v4",
        gold_version="gold-v3-overlay-mixed-09",
        model_id=MODEL_ID,
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
