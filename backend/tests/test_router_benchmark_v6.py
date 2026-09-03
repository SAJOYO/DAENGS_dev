"""v6 prompt-regression runner: production `semantic-router-ko-v5` vs. the frozen v3 contract.

No Gemini calls here (FakeClient only). The ONE changed variable versus the v5 run is
the production prompt (v5 narrows Life and declares general care unsupported); model id,
gold set, gates, schema, and the single-schema-retry policy are unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from daengs_backend.orchestration.semantic import PROMPT_VERSION, SemanticRoutingDecision
from tools.router_benchmark.evaluate import apply_acceptance_gates, evaluate_benchmark
from tools.router_benchmark.runner import build_artifacts
from tools.router_benchmark.runner_v2 import GENERATION_CONFIG
from tools.router_benchmark.runner_v5 import BENCHMARK_ID as V5_BENCHMARK_ID
from tools.router_benchmark.runner_v5 import run_cases
from tools.router_benchmark.runner_v6 import BENCHMARK_ID, GOLD_VERSION, MODEL_ID
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


def test_v6_is_the_next_run_identifier_with_v5_prompt_and_unchanged_model_gold() -> None:
    assert V5_BENCHMARK_ID == "orchestration-router-v5"
    assert BENCHMARK_ID == "orchestration-router-v6"
    assert PROMPT_VERSION == "semantic-router-ko-v5"
    assert MODEL_ID == "gemini-3.1-flash-lite"
    assert GOLD_VERSION == "gold-v3-overlay-mixed-09"
    assert len(load_gold_v3_cases()) == 80
    # The recorded v5 run certified prompt v4; v6 is the first run of prompt v5.
    recorded_v5 = json.loads((EVALS_DIR / "summary_v5.json").read_text(encoding="utf-8"))
    assert recorded_v5["prompt_version"] == "semantic-router-ko-v4"
    assert recorded_v5["benchmark_id"] == "orchestration-router-v5"


def test_v6_provider_call_uses_the_production_v5_prompt_and_schema() -> None:
    case = next(c for c in load_gold_v3_cases() if c.case_id == "training_01")
    client = FakeClient([_response({"execute": ["training"], "handoffs": []})])
    attempts, _, social = run_cases([case], client=client, model_id=MODEL_ID)
    call = client.models.calls[0]
    assert call["model"] == "gemini-3.1-flash-lite"
    assert "PROMPT_VERSION: semantic-router-ko-v5" in call["contents"]
    assert "husbandry" in call["contents"]
    assert call["config"].response_json_schema == SemanticRoutingDecision.model_json_schema()
    assert attempts[case.case_id][0].schema_valid is True
    assert social == {case.case_id: None}


def test_v6_artifacts_and_frozen_gates() -> None:
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
    assert summary["prompt_version"] == "semantic-router-ko-v5"
    assert summary["benchmark_id"] == "orchestration-router-v6"
    assert "semantic-router-ko-v5" in report

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
