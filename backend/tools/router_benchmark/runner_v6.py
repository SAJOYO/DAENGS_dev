"""Prompt-regression run: production `semantic-router-ko-v5` against the unchanged v3 gold/gates.

v5 (PR #172) changes the production router in exactly one place: the Life definition is
narrowed to FORMAL institutional / legal / administrative / policy / contractual evidence,
and general pet husbandry/care recommendations (walk frequency, feeding, sleep, water
intake, breed/age/size-specific care) are declared unsupported — they must yield an empty
decision instead of being absorbed by Life (a two-call live probe of production v4 routed
both "푸들 산책은 몇 회가 좋아?" and "강아지는 하루에 몇 번 산책해야 해?" to Life). No new
ExecuteName, no schema change, no answer generation.

Because the production prompt changed, the accepted v1 routing behavior must be re-checked:
the SAME 80 corrected v3 gold cases, the SAME frozen acceptance gates, the SAME model
(`gemini-3.1-flash-lite`), run exactly once. The gold set contains no general-care case
(those live in `tests/test_orchestration_care_boundary.py`), so this run certifies that the
narrower Life boundary did not move any accepted case; `social_intent` is still expected
null everywhere. Like runner_v5 this drives the PRODUCTION prompt builder and schema. v1–v5
artifacts are not modified.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from daengs_backend.orchestration.semantic import PROMPT_VERSION, ROUTER_MODEL_ID

from .runner import build_artifacts, create_gemini_client, write_artifacts
from .runner_v2 import GENERATION_CONFIG
from .runner_v5 import run_cases, social_intent_section
from .schemas import load_gold_v3_cases

MODEL_ID = ROUTER_MODEL_ID
BENCHMARK_ID = "orchestration-router-v6"
GOLD_VERSION = "gold-v3-overlay-mixed-09"
RESULTS_DIR = Path(__file__).resolve().parents[2] / "evals" / "orchestration_router"
RESULTS_PATH = RESULTS_DIR / "results_v6.jsonl"
SUMMARY_PATH = RESULTS_DIR / "summary_v6.json"
REPORT_PATH = RESULTS_DIR / "phase2_v6_report.md"


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
