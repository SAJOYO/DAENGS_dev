"""v9 prompt-regression runner: production `semantic-router-ko-v8` vs. the frozen v3 contract.

No Gemini calls here (FakeClient only). The ONE changed variable versus the recorded v8
run is the production prompt (v8 adds the explicit-exclusion sentence); model id, gold
set, gates, schema shape and the single-schema-retry policy are unchanged.

Two things are worth pinning beyond the usual "new paths, real prompt, no overwrite":
the runner scores the router's decision with the general fallback OFF (an empty decision
stays an empty plan, exactly as v1–v8 scored it), and the frozen evaluator now admits
`general` so a flag-on plan would be a precision miss, never an invented capability.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from daengs_backend.orchestration.semantic import PROMPT_VERSION, SemanticRoutingDecision
from tools.router_benchmark.evaluate import apply_acceptance_gates, evaluate_benchmark
from tools.router_benchmark.runner import build_artifacts
from tools.router_benchmark.runner_v2 import GENERATION_CONFIG
from tools.router_benchmark.runner_v5 import run_cases
from tools.router_benchmark.runner_v8 import BENCHMARK_ID as V8_BENCHMARK_ID
from tools.router_benchmark.runner_v9 import (
    BENCHMARK_ID,
    GOLD_VERSION,
    MODEL_ID,
    REPORT_PATH,
    RESULTS_PATH,
    SUMMARY_PATH,
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


def test_v9_is_the_next_run_identifier_with_the_v8_prompt_and_unchanged_model_gold() -> None:
    assert V8_BENCHMARK_ID == "orchestration-router-v8"
    assert BENCHMARK_ID == "orchestration-router-v9"
    assert MODEL_ID == "gemini-3.1-flash-lite"
    assert GOLD_VERSION == "gold-v3-overlay-mixed-09"
    assert len(load_gold_v3_cases()) == 80
    # The v8 record stays exactly as it was accepted.
    recorded_v8 = json.loads((EVALS_DIR / "summary_v8.json").read_text(encoding="utf-8"))
    assert recorded_v8["prompt_version"] == "semantic-router-ko-v7"
    assert recorded_v8["verdict"] == "PASS"
    # The v9 run itself is frozen as having sent v8 — that record is immutable even
    # though production has since advanced (D-057 → v9, run by runner_v10).
    recorded_v9 = json.loads((EVALS_DIR / "summary_v9.json").read_text(encoding="utf-8"))
    assert recorded_v9["prompt_version"] == "semantic-router-ko-v8"
    assert recorded_v9["verdict"] == "PASS"
    assert recorded_v9["metrics"]["exact_route_plan_match"] == 0.975


def test_v9_writes_only_new_artifact_paths() -> None:
    """A paid run must never be able to overwrite historical evidence."""
    for path in (RESULTS_PATH, SUMMARY_PATH, REPORT_PATH):
        assert path.name.endswith(("_v9.jsonl", "_v9.json", "_v9_report.md")), path.name
    historical = {
        EVALS_DIR / f"{stem}_v{n}{suffix}"
        for n in range(1, 9)
        for stem, suffix in (("results", ".jsonl"), ("summary", ".json"))
    }
    assert not historical & {RESULTS_PATH, SUMMARY_PATH, REPORT_PATH}


def test_v9_provider_call_uses_the_production_prompt_and_schema() -> None:
    case = next(c for c in load_gold_v3_cases() if c.case_id == "mixed_10")
    client = FakeClient([_response({"execute": ["life", "walk"], "handoffs": ["gait"]})])
    attempts, _, social = run_cases([case], client=client, model_id=MODEL_ID)
    call = client.models.calls[0]
    assert call["model"] == "gemini-3.1-flash-lite"
    assert f"PROMPT_VERSION: {PROMPT_VERSION}" in call["contents"]
    # v6's walking-window sentence and v7's Place destination survive v8 untouched.
    assert "choosing a suitable walking" in call["contents"]
    assert "execute.place:" in call["contents"]
    # and the one v8 addition is actually sent. (Whether `general` is offered is a v9 /
    # runner_v10 matter — this runner drives whatever the production prompt is.)
    assert "explicitly excludes a topic" in call["contents"]
    assert call["config"].response_json_schema == SemanticRoutingDecision.model_json_schema()
    [attempt] = attempts[case.case_id]
    assert attempt.schema_valid is True
    assert attempt.plan == case.gold_route_plan.model_copy(
        update={
            "router": "llm",
            "model": "gemini-3.1-flash-lite",
            "prompt_version": PROMPT_VERSION,
        }
    )
    assert social == {case.case_id: None}


def test_v9_scores_the_router_decision_with_the_fallback_off() -> None:
    """An empty decision stays an empty plan in the runner — the flag is a runtime setting
    the benchmark never reads, so v9 measures exactly what v1–v8 measured."""
    case = next(c for c in load_gold_v3_cases() if c.case_id == "training_01")
    client = FakeClient([_response({"execute": [], "handoffs": []})])
    attempts, _, _ = run_cases([case], client=client, model_id=MODEL_ID)
    [attempt] = attempts[case.case_id]
    assert attempt.schema_valid is True and attempt.plan is not None
    assert attempt.plan.requests == [] and attempt.plan.handoffs == []


def test_v9_scores_a_general_request_on_the_frozen_eighty_as_a_precision_miss() -> None:
    """Same reasoning as v8's `place` case: a real capability the gold does not know must
    cost precision, not trip the zero-tolerance invented-capability gate."""
    case = next(c for c in load_gold_v3_cases() if c.case_id == "training_01")
    padded = {
        "requests": [
            {"capability": "training", "payload": {"question": case.query}, "timeout_ms": None},
            {"capability": "general", "payload": {"question": case.query}, "timeout_ms": None},
        ],
        "handoffs": [],
        "clarify": None,
        "router": "llm",
        "model": MODEL_ID,
    }
    result = evaluate_benchmark([case], {case.case_id: [padded]})
    assert result.summary.invented_unsupported_capability_count == 0
    assert result.summary.executable_precision < 1.0
    assert result.summary.executable_recall == 1.0


def test_v9_artifacts_and_frozen_gates() -> None:
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
    assert summary["benchmark_id"] == "orchestration-router-v9"
    assert summary["performance"]["total_tokens"] == 110  # usage_metadata is what gets recorded
    assert PROMPT_VERSION in report

    # The gates themselves are untouched: a perfect run still passes all 15.
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
