"""v7 prompt-regression runner: production `semantic-router-ko-v6` vs. the frozen v3 contract.

No Gemini calls here (FakeClient only). The ONE changed variable versus the recorded v6
run is the production prompt (v6 separates routine exercise advice from today's walking
window); model id, gold set, gates, schema, and the single-schema-retry policy are unchanged.

**What pins v6 here is the recorded artifact, not the live constant** (PR #204). Every
runner drives whatever `PROMPT_VERSION` production currently exports, so once production
moved to v7 these tests could no longer assert the live constant equals v6 without
breaking on every future prompt bump. `evals/orchestration_router/summary_v7.json` is the
frozen record of what this run actually sent, and that is what the version assertions read
now; the call-shape assertions follow the live constant on purpose, because that is the
real property being tested (the runner sends production's prompt, whatever it is).
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
from daengs_evals.router_benchmark.runner_v6 import BENCHMARK_ID as V6_BENCHMARK_ID
from daengs_evals.router_benchmark.runner_v7 import BENCHMARK_ID, GOLD_VERSION, MODEL_ID
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


def test_v7_is_the_next_run_identifier_with_v6_prompt_and_unchanged_model_gold() -> None:
    assert V6_BENCHMARK_ID == "orchestration-router-v6"
    assert BENCHMARK_ID == "orchestration-router-v7"
    assert MODEL_ID == "gemini-3.1-flash-lite"
    assert GOLD_VERSION == "gold-v3-overlay-mixed-09"
    assert len(load_gold_v3_cases()) == 80
    recorded_v6 = json.loads((EVALS_DIR / "summary_v6.json").read_text(encoding="utf-8"))
    assert recorded_v6["prompt_version"] == "semantic-router-ko-v5"
    assert recorded_v6["verdict"] == "FAIL"
    # The v7 run itself is frozen as having sent v6 — that record is immutable even
    # though production has since advanced (PR #204 → v7).
    recorded_v7 = json.loads((EVALS_DIR / "summary_v7.json").read_text(encoding="utf-8"))
    assert recorded_v7["prompt_version"] == "semantic-router-ko-v6"
    assert recorded_v7["verdict"] == "PASS"


def test_v7_provider_call_uses_the_production_prompt_and_schema() -> None:
    case = next(c for c in load_gold_v3_cases() if c.case_id == "mixed_10")
    client = FakeClient([_response({"execute": ["life", "walk"], "handoffs": ["gait"]})])
    attempts, _, social = run_cases([case], client=client, model_id=MODEL_ID)
    call = client.models.calls[0]
    assert call["model"] == "gemini-3.1-flash-lite"
    assert f"PROMPT_VERSION: {PROMPT_VERSION}" in call["contents"]
    assert "choosing a suitable walking" in call["contents"]
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


def test_v7_artifacts_and_frozen_gates() -> None:
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
    assert summary["benchmark_id"] == "orchestration-router-v7"
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
