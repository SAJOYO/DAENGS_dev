"""v10 prompt-regression runner: production `semantic-router-ko-v9` vs. the frozen v3 contract.

No Gemini calls here (FakeClient only). The ONE changed variable versus the recorded v9
run is the production prompt (v9 adds `general` as an additive destination, D-057 ①);
model id, gold set, gates and the single-schema-retry policy are unchanged.

What is new and worth pinning: the runner reports TWO views from one paid run — raw
(planner with the flag on, `general` counted) and general-stripped (every `general`
request removed, which is byte-for-byte the flag-off production plan). The stripped view
is the one that must not regress against v9.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from daengs_backend.orchestration.contracts import CapabilityName
from daengs_backend.orchestration.semantic import PROMPT_VERSION, SemanticRoutingDecision
from tools.router_benchmark.evaluate import apply_acceptance_gates, evaluate_benchmark
from tools.router_benchmark.runner import build_artifacts
from tools.router_benchmark.runner_v2 import GENERATION_CONFIG
from tools.router_benchmark.runner_v9 import BENCHMARK_ID as V9_BENCHMARK_ID
from tools.router_benchmark.runner_v10 import (
    BENCHMARK_ID,
    GOLD_VERSION,
    MODEL_ID,
    REPORT_PATH,
    RESULTS_PATH,
    STRIPPED_BENCHMARK_ID,
    SUMMARY_PATH,
    general_case_ids,
    run_cases,
    strip_general_everywhere,
)
from tools.router_benchmark.schemas import load_benchmark_config, load_gold_v3_cases

EVALS_DIR = Path(__file__).parents[1] / "evals" / "orchestration_router"


class FakeModels:
    def __init__(self, responses: list[object]) -> None:
        self.responses = iter(responses)
        self.calls: list[dict] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return next(self.responses)


class FakeClient:
    def __init__(self, responses: list[object]) -> None:
        self.models = FakeModels(responses)


def _response(payload: object) -> SimpleNamespace:
    return SimpleNamespace(
        parsed=payload,
        text=None,
        usage_metadata=SimpleNamespace(
            prompt_token_count=100, candidates_token_count=10, total_token_count=110
        ),
    )


def test_v10_is_the_next_run_identifier_with_the_v9_prompt_and_unchanged_model_gold() -> None:
    assert V9_BENCHMARK_ID == "orchestration-router-v9"
    assert BENCHMARK_ID == "orchestration-router-v10"
    assert STRIPPED_BENCHMARK_ID == "orchestration-router-v10-general-stripped"
    assert PROMPT_VERSION == "semantic-router-ko-v9"
    assert MODEL_ID == "gemini-3.1-flash-lite"
    assert GOLD_VERSION == "gold-v3-overlay-mixed-09"
    assert len(load_gold_v3_cases()) == 80
    recorded_v9 = json.loads((EVALS_DIR / "summary_v9.json").read_text(encoding="utf-8"))
    assert recorded_v9["prompt_version"] == "semantic-router-ko-v8"
    assert recorded_v9["verdict"] == "PASS"


def test_v10_recorded_run_is_the_narrowed_prompt_with_both_views_passing() -> None:
    """The frozen v10 record is the NARROWED v9 prompt's run, not the first draft's.

    The first v9 draft attached `general` to 32/80 (every `training_*` case; raw exact
    0.6125) and was narrowed once before any run was frozen; its artifacts were
    overwritten. What is recorded: 0/80 cases gained `general`, so raw equals stripped,
    both PASS, and the stripped view did not regress against v9 (0.975).
    """
    recorded = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    assert recorded["prompt_version"] == "semantic-router-ko-v9"
    assert recorded["benchmark_id"] == BENCHMARK_ID
    assert recorded["general_selected_case_ids"] == []
    assert recorded["verdict"] == "PASS"
    assert recorded["general_stripped"]["verdict"] == "PASS"
    assert recorded["general_stripped"]["benchmark_id"] == STRIPPED_BENCHMARK_ID
    assert recorded["metrics"] == recorded["general_stripped"]["metrics"]
    assert recorded["metrics"]["exact_route_plan_match"] >= 0.975
    assert recorded["metrics"]["executable_recall"] == 1.0
    assert recorded["retry_count"] == 0
    rows = [
        json.loads(line)
        for line in RESULTS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 80
    assert not any(
        request["capability"] == "general"
        for row in rows
        if row["prediction"]
        for request in row["prediction"]["requests"]
    )


def test_v10_writes_only_new_artifact_paths() -> None:
    for path in (RESULTS_PATH, SUMMARY_PATH, REPORT_PATH):
        assert path.name.endswith(("_v10.jsonl", "_v10.json", "_v10_report.md")), path.name
    historical = {
        EVALS_DIR / f"{stem}_v{n}{suffix}"
        for n in range(1, 10)
        for stem, suffix in (("results", ".jsonl"), ("summary", ".json"))
    }
    assert not historical & {RESULTS_PATH, SUMMARY_PATH, REPORT_PATH}


def test_v10_provider_call_uses_the_production_prompt_and_offers_general() -> None:
    case = next(c for c in load_gold_v3_cases() if c.case_id == "mixed_10")
    client = FakeClient([_response({"execute": ["life", "walk"], "handoffs": ["gait"]})])
    attempts, _, social = run_cases([case], client=client, model_id=MODEL_ID)
    call = client.models.calls[0]
    assert call["model"] == "gemini-3.1-flash-lite"
    assert f"PROMPT_VERSION: {PROMPT_VERSION}" in call["contents"]
    assert "choosing a suitable walking" in call["contents"]  # v6
    assert "execute.place:" in call["contents"]  # v7
    assert "explicitly excludes a topic" in call["contents"]  # v8
    assert "execute.general:" in call["contents"]  # v9
    assert call["config"].response_json_schema == SemanticRoutingDecision.model_json_schema()
    assert (
        "general" in call["config"].response_json_schema["properties"]["execute"]["items"]["enum"]
    )
    [attempt] = attempts[case.case_id]
    assert attempt.schema_valid is True
    assert attempt.plan == case.gold_route_plan.model_copy(
        update={"router": "llm", "model": "gemini-3.1-flash-lite", "prompt_version": PROMPT_VERSION}
    )
    assert social == {case.case_id: None}


def test_v10_raw_view_keeps_general_and_stripped_view_is_the_flag_off_plan() -> None:
    """One paid decision, two scored plans: the raw one carries `general` last; the stripped
    one is exactly what the planner builds with the flag off."""
    case = next(c for c in load_gold_v3_cases() if c.case_id == "walk_01")
    client = FakeClient([_response({"execute": ["general", "walk"], "handoffs": []})])
    attempts, _, _ = run_cases([case], client=client, model_id=MODEL_ID)
    [raw] = attempts[case.case_id]
    assert raw.plan is not None
    assert [r.capability for r in raw.plan.requests] == [
        CapabilityName.WALK,
        CapabilityName.GENERAL,
    ]
    assert general_case_ids(attempts) == [case.case_id]

    stripped = strip_general_everywhere(attempts)
    [flag_off] = stripped[case.case_id]
    assert flag_off.plan is not None
    assert [r.capability for r in flag_off.plan.requests] == [CapabilityName.WALK]
    assert flag_off.requested_capability_names == ["walk"]
    assert general_case_ids(stripped) == []

    raw_eval = evaluate_benchmark(
        [case], attempts, prompt_version=PROMPT_VERSION, model_id=MODEL_ID
    )
    stripped_eval = evaluate_benchmark(
        [case], stripped, prompt_version=PROMPT_VERSION, model_id=MODEL_ID
    )
    assert raw_eval.summary.invented_unsupported_capability_count == 0  # allowed, not invented
    assert (
        raw_eval.summary.executable_precision < 1.0 and raw_eval.summary.exact_route_plan_match == 0
    )
    assert stripped_eval.summary.exact_route_plan_match == 1.0


def test_v10_empty_decision_becomes_general_in_the_raw_view_only() -> None:
    """The planner's empty→general rule fires in the raw view (flag on) — the D-057 policy —
    and the stripped view returns to the empty plan v1–v9 scored."""
    case = next(c for c in load_gold_v3_cases() if c.case_id == "training_01")
    client = FakeClient([_response({"execute": [], "handoffs": []})])
    attempts, _, _ = run_cases([case], client=client, model_id=MODEL_ID)
    [raw] = attempts[case.case_id]
    assert raw.plan is not None
    assert [r.capability for r in raw.plan.requests] == [CapabilityName.GENERAL]
    # the router did not *select* general here, so it is not counted as a selection
    assert general_case_ids(attempts) == []
    [flag_off] = strip_general_everywhere(attempts)[case.case_id]
    assert flag_off.plan is not None and flag_off.plan.requests == []


def test_v10_artifacts_and_frozen_gates() -> None:
    case = next(c for c in load_gold_v3_cases() if c.case_id == "training_01")
    client = FakeClient([_response({"execute": ["training"], "handoffs": []})])
    attempts, performance, _ = run_cases([case], client=client, model_id=MODEL_ID)
    _, summary, report = build_artifacts(
        [case],
        attempts,
        performance,
        prompt_version=PROMPT_VERSION,
        generation_config=GENERATION_CONFIG,
        benchmark_id=BENCHMARK_ID,
        gold_version=GOLD_VERSION,
        model_id=MODEL_ID,
    )
    assert summary["prompt_version"] == PROMPT_VERSION
    assert summary["benchmark_id"] == "orchestration-router-v10"
    assert summary["performance"]["total_tokens"] == 110
    assert PROMPT_VERSION in report

    cases = load_gold_v3_cases()
    perfect = evaluate_benchmark(
        cases,
        {c.case_id: [c.gold_route_plan] for c in cases},
        prompt_version=PROMPT_VERSION,
        model_id=MODEL_ID,
    )
    verdict = apply_acceptance_gates(perfect.summary, load_benchmark_config())
    assert verdict.verdict == "PASS"
    assert len(verdict.checks) == 15
