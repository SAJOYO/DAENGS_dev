"""Prompt-regression run: production `semantic-router-ko-v7` against the unchanged v3 gold/gates.

v7 (PR #204, D-051) adds the fourth EXECUTE destination, `place`. Place was already an
executable capability with a payload type, adapter and bounded projection (PR #196); it
simply could not be *selected* from free text, so "오늘 산책하기 좋은 곳이 어디야?" ran
Walk alone. v7 adds one destination plus three boundary sentences (Place answers "where
should I go"; a place noun that is only the setting of a Training/Walk/Gait request is
not a Place target; both are selected when one utterance asks both). No model change, no
schema change beyond the one enum value, no answer generation.

**This run exists to prove the other 80 cases did not move.** Adding a destination can
only cost precision here: the frozen gold has no Place case, so every Place selection on
these 80 is a false positive. The headroom is small and worth stating before spending
the call — gold carries 76 executes against an `executable_precision` gate of 0.95, and
the accepted v7 run already spends 2 of the 4 tolerable false positives (`boundary_05`
gained Walk, `mixed_09` gained Training). Two spurious Place selections are absorbable;
three are not. Watch `walk_03` ("오늘 걷기 좋은 구간 골라줘"), `multi_07` and `multi_11`
(공원 as setting) — those are the rows whose phrasing sits closest to the new
destination, and the "setting is not a target" sentence exists to hold them.

The Place acceptance set itself is `gold_place_v1.jsonl`, certified deterministically by
`tests/test_router_benchmark_place_gold.py`; it is deliberately not merged into these 80.
Same 80 corrected v3 gold cases, same frozen gates, same `gemini-3.1-flash-lite`, run
exactly once. v1–v7 artifacts are not modified.
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
BENCHMARK_ID = "orchestration-router-v8"
GOLD_VERSION = "gold-v3-overlay-mixed-09"
RESULTS_DIR = EVALS_DIR / "orchestration_router"
RESULTS_PATH = RESULTS_DIR / "results_v8.jsonl"
SUMMARY_PATH = RESULTS_DIR / "summary_v8.json"
REPORT_PATH = RESULTS_DIR / "phase2_v8_report.md"


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
