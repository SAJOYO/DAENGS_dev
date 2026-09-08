"""v8 prompt-regression runner: production `semantic-router-ko-v7` vs. the frozen v3 contract.

No Gemini calls here (FakeClient only). The ONE changed variable versus the recorded v7
run is the production prompt (v7 adds the `place` destination); model id, gold set,
gates, schema shape and the single-schema-retry policy are unchanged.

The point of the v8 run is narrow and worth naming: the frozen 80 contain no Place case,
so on this set every Place selection is a false positive. These tests make sure the run
is pointed at new artifact paths, drives the real production prompt, and cannot quietly
overwrite v1–v7 evidence.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from daengs_backend.orchestration.semantic import PROMPT_VERSION, SemanticRoutingDecision
from daengs_evals.router_benchmark.evaluate import apply_acceptance_gates, evaluate_benchmark
from daengs_evals.router_benchmark.runner import build_artifacts
from daengs_evals.router_benchmark.runner_v2 import GENERATION_CONFIG
from daengs_evals.router_benchmark.runner_v5 import run_cases
from daengs_evals.router_benchmark.runner_v7 import BENCHMARK_ID as V7_BENCHMARK_ID
from daengs_evals.router_benchmark.runner_v8 import (
    BENCHMARK_ID,
    GOLD_VERSION,
    MODEL_ID,
    REPORT_PATH,
    RESULTS_PATH,
    SUMMARY_PATH,
)
from daengs_evals.router_benchmark.schemas import load_benchmark_config, load_gold_v3_cases

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


def test_v8_is_the_next_run_identifier_with_the_v7_prompt_and_unchanged_model_gold() -> None:
    assert V7_BENCHMARK_ID == "orchestration-router-v7"
    assert BENCHMARK_ID == "orchestration-router-v8"
    assert MODEL_ID == "gemini-3.1-flash-lite"
    assert GOLD_VERSION == "gold-v3-overlay-mixed-09"
    assert len(load_gold_v3_cases()) == 80
    # The v7 record stays exactly as it was accepted.
    recorded_v7 = json.loads((EVALS_DIR / "summary_v7.json").read_text(encoding="utf-8"))
    assert recorded_v7["prompt_version"] == "semantic-router-ko-v6"
    assert recorded_v7["verdict"] == "PASS"
    # The v8 run itself is frozen as having sent v7 — that record is immutable even
    # though production has since advanced (PR #279 → v8, run by runner_v9).
    recorded_v8 = json.loads((EVALS_DIR / "summary_v8.json").read_text(encoding="utf-8"))
    assert recorded_v8["prompt_version"] == "semantic-router-ko-v7"
    assert recorded_v8["verdict"] == "PASS"


def test_v8_writes_only_new_artifact_paths() -> None:
    """A paid run must never be able to overwrite historical evidence."""
    for path in (RESULTS_PATH, SUMMARY_PATH, REPORT_PATH):
        assert path.name.endswith(("_v8.jsonl", "_v8.json", "_v8_report.md")), path.name
    historical = {
        EVALS_DIR / f"{stem}_v{n}{suffix}"
        for n in range(1, 8)
        for stem, suffix in (("results", ".jsonl"), ("summary", ".json"))
    }
    assert not historical & {RESULTS_PATH, SUMMARY_PATH, REPORT_PATH}


def test_v8_provider_call_uses_the_production_prompt_and_schema() -> None:
    case = next(c for c in load_gold_v3_cases() if c.case_id == "mixed_10")
    client = FakeClient([_response({"execute": ["life", "walk"], "handoffs": ["gait"]})])
    attempts, _, social = run_cases([case], client=client, model_id=MODEL_ID)
    call = client.models.calls[0]
    assert call["model"] == "gemini-3.1-flash-lite"
    assert f"PROMPT_VERSION: {PROMPT_VERSION}" in call["contents"]
    # v6's walking-window sentence survives v7 untouched.
    assert "choosing a suitable walking" in call["contents"]
    # and the new destination is actually offered to the model.
    assert "execute.place:" in call["contents"]
    assert call["config"].response_json_schema == SemanticRoutingDecision.model_json_schema()
    [attempt] = attempts[case.case_id]
    assert attempt.schema_valid is True
    assert attempt.plan == case.gold_route_plan.model_copy(
        # 얼어붙은 gold 는 관측 메타데이터를 안 들고 있거나 그때 값으로 들고 있다 — 라우팅
        # 의미가 아니라 경위라서, 지금 값으로 맞춘 뒤 비교한다 (`prompt_version` 은 #238).
        update={
            "router": "llm",
            "model": "gemini-3.1-flash-lite",
            "prompt_version": PROMPT_VERSION,
        }
    )
    assert social == {case.case_id: None}


def test_v8_scores_a_place_selection_on_the_frozen_eighty_as_a_precision_miss() -> None:
    """Not as an invented capability — that distinction is the whole point of the
    `evaluate.ALLOWED_EXECUTE` change, and getting it wrong would fail the run for the
    wrong reason (a zero-tolerance gate) instead of reporting a routing miss."""
    case = next(c for c in load_gold_v3_cases() if c.case_id == "training_01")
    spurious = {
        "requests": [
            {"capability": "training", "payload": {"question": case.query}, "timeout_ms": None},
            {
                "capability": "place",
                "payload": {"query": case.query, "lat": 37.5665, "lon": 126.978},
                "timeout_ms": None,
            },
        ],
        "handoffs": [],
        "clarify": None,
        "router": "llm",
        "model": MODEL_ID,
    }
    result = evaluate_benchmark([case], {case.case_id: [spurious]})
    assert result.summary.invented_unsupported_capability_count == 0
    assert result.summary.executable_precision < 1.0
    assert result.summary.executable_recall == 1.0


def test_v8_artifacts_and_frozen_gates() -> None:
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
    assert summary["benchmark_id"] == "orchestration-router-v8"
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
