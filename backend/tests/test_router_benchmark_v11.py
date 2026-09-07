"""v11 prompt-regression runner: production `semantic-router-ko-v10` vs. the frozen v3 contract.

No Gemini calls here. The ONE changed variable versus the recorded v10 run is the production
prompt — v10 narrows Place to "is this venue for the dog?" (D-057 ④ follow-up). Gold, gates,
model and the two-view (raw / general-stripped) reporting are v10's, imported rather than copied.

**This file is where the live prompt is pinned.** `test_router_benchmark_v10.py` pins the string
its own run recorded; the assertion that production has not moved since the newest recorded run
belongs to the newest run's test, which is this one. Moving the prompt without a new run breaks
here, and that is the point.
"""

from __future__ import annotations

import json
from pathlib import Path

from daengs_backend.orchestration.semantic import PROMPT_VERSION
from tools.router_benchmark.runner_v10 import BENCHMARK_ID as V10_BENCHMARK_ID
from tools.router_benchmark.runner_v11 import (
    BENCHMARK_ID,
    REPORT_PATH,
    RESULTS_PATH,
    STRIPPED_BENCHMARK_ID,
    SUMMARY_PATH,
)
from tools.router_benchmark.schemas import load_gold_v3_cases

EVALS_DIR = Path(__file__).parents[1] / "evals" / "orchestration_router"


def test_v11_is_the_next_run_identifier_and_pins_the_live_prompt() -> None:
    assert V10_BENCHMARK_ID == "orchestration-router-v10"
    assert BENCHMARK_ID == "orchestration-router-v11"
    assert STRIPPED_BENCHMARK_ID == "orchestration-router-v11-general-stripped"
    assert PROMPT_VERSION == "semantic-router-ko-v10"
    assert len(load_gold_v3_cases()) == 80


def test_v11_recorded_run_passes_both_views_without_losing_accuracy() -> None:
    """총 정확도는 v10 과 같고, 틀린 한 건이 `walk_03` 에서 `mixed_09` 로 옮겨 갔다.

    `walk_03`("이 위치의 … 걷기 좋은 …")은 v10 에서 `place` 를 하나 더 붙였는데 v11 에서
    사라졌다 — 이 카드가 노린 방향 그대로다. 대신 `mixed_09` 가 `training` 을 하나 더
    붙였다. 둘 다 EXTRA_EXECUTE 이고, `mixed_09` 는 문서가 이미 v4·v5 run 부터 흔들린
    경계 사례로 적어 둔 건이다. `exact_mixed_...` 가 1.0 → 0.9 인 것은 그 한 건이 mixed
    부분집합으로 옮겨 갔기 때문이고, 게이트(≥0.90)는 통과한다. **튜닝하지 않고 적는다**
    (D-057 ③).
    """
    recorded = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    previous = json.loads((EVALS_DIR / "summary_v10.json").read_text(encoding="utf-8"))

    assert recorded["prompt_version"] == "semantic-router-ko-v10"
    assert recorded["benchmark_id"] == BENCHMARK_ID
    assert recorded["verdict"] == "PASS"
    assert recorded["general_stripped"]["verdict"] == "PASS"
    assert recorded["general_stripped"]["benchmark_id"] == STRIPPED_BENCHMARK_ID
    # 폴백은 라우터 목적지이지만 골드 80건에는 붙을 자리가 없다 — v10 과 같이 0건이라
    # raw 와 stripped 가 같은 수치다.
    assert recorded["general_selected_case_ids"] == []
    assert recorded["metrics"] == recorded["general_stripped"]["metrics"]

    assert recorded["metrics"]["exact_route_plan_match"] == previous["metrics"]["exact_route_plan_match"]
    assert recorded["metrics"]["exact_route_plan_match"] >= 0.975
    assert recorded["metrics"]["executable_recall"] == 1.0
    assert recorded["metrics"]["exact_mixed_execute_handoff_match"] >= 0.90
    assert recorded["retry_count"] == 0
    assert recorded["failing_case_ids"] == ["mixed_09"]
    assert previous["failing_case_ids"] == ["walk_03"]
    assert recorded["failure_categories"] == {"EXTRA_EXECUTE": 1}


def test_v11_artifacts_are_the_three_files_the_runner_writes() -> None:
    assert RESULTS_PATH.name == "results_v11.jsonl"
    assert SUMMARY_PATH.name == "summary_v11.json"
    assert REPORT_PATH.name == "phase2_v11_report.md"
    for path in (RESULTS_PATH, SUMMARY_PATH, REPORT_PATH):
        assert path.exists()
    rows = [json.loads(line) for line in RESULTS_PATH.read_text(encoding="utf-8").splitlines() if line]
    assert len(rows) == 80
    assert {row["prompt_version"] for row in rows} == {"semantic-router-ko-v10"}
