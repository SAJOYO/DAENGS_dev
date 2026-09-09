"""v5 prompt-regression runner: the recorded run certified `semantic-router-ko-v4`.

No Gemini calls here (FakeClient only). The runner drives whatever the PRODUCTION prompt
is at import time (v4 when the v5 artifacts were recorded, v5 since PR #172 — its
regression is runner_v6), so these tests pin the production `PROMPT_VERSION` symbol and
check the recorded v5 artifacts separately. Model id, gold set, gates, and the
single-schema-retry policy must match the accepted v4 run byte-for-byte.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from daengs_backend.orchestration.semantic import PROMPT_VERSION, SemanticRoutingDecision
from daengs_evals.router_benchmark.evaluate import apply_acceptance_gates, evaluate_benchmark
from daengs_evals.router_benchmark.runner import build_artifacts
from daengs_evals.router_benchmark.runner_v2 import GENERATION_CONFIG
from daengs_evals.router_benchmark.runner_v4 import MODEL_ID as V4_MODEL_ID
from daengs_evals.router_benchmark.runner_v5 import (
    BENCHMARK_ID,
    GOLD_VERSION,
    MODEL_ID,
    run_cases,
    social_intent_section,
)
from daengs_evals.router_benchmark.schemas import (
    GoldCase,
    load_benchmark_config,
    load_gold_v3_cases,
)

EVALS_DIR = Path(__file__).parents[1] / "evals" / "orchestration_router"


@dataclass
class FakeResponse:
    parsed: object | None = None
    text: str | None = None
    usage_metadata: object | None = None


class FakeModels:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = iter(responses)
        self.calls: list[dict] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return next(self.responses)


class FakeClient:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.models = FakeModels(responses)


def _case(case_id: str) -> GoldCase:
    return next(case for case in load_gold_v3_cases() if case.case_id == case_id)


def _response(payload: object) -> FakeResponse:
    return FakeResponse(
        parsed=payload,
        usage_metadata=SimpleNamespace(
            prompt_token_count=100, candidates_token_count=10, total_token_count=110
        ),
    )


def test_v5_keeps_the_v4_model_and_the_v3_gold_and_gates() -> None:
    assert MODEL_ID == V4_MODEL_ID == "gemini-3.1-flash-lite"
    # The recorded v5 run certified prompt v4; production has since moved to v5 (runner_v6).
    recorded = json.loads((EVALS_DIR / "summary_v5.json").read_text(encoding="utf-8"))
    assert recorded["prompt_version"] == "semantic-router-ko-v4"
    assert PROMPT_VERSION.startswith("semantic-router-ko-v")
    assert GOLD_VERSION == "gold-v3-overlay-mixed-09"
    assert BENCHMARK_ID == "orchestration-router-v5"
    assert len(load_gold_v3_cases()) == 80


def test_v5_provider_call_uses_the_production_prompt_and_schema() -> None:
    case = _case("training_01")
    client = FakeClient([_response({"execute": ["training"], "handoffs": []})])
    attempts, _, social = run_cases([case], client=client)
    call = client.models.calls[0]
    assert call["model"] == "gemini-3.1-flash-lite"
    assert f"PROMPT_VERSION: {PROMPT_VERSION}" in call["contents"]
    assert "social_intent" in call["contents"]
    assert call["config"].response_json_schema == SemanticRoutingDecision.model_json_schema()
    assert attempts[case.case_id][0].schema_valid is True
    assert attempts[case.case_id][0].plan == case.gold_route_plan.model_copy(
        # 얼어붙은 gold 는 관측 메타데이터를 안 들고 있거나 그때 값으로 들고 있다 — 라우팅
        # 의미가 아니라 경위라서, 지금 값으로 맞춘 뒤 비교한다 (`prompt_version` 은 #238).
        update={
            "router": "llm",
            "model": "gemini-3.1-flash-lite",
            "prompt_version": PROMPT_VERSION,
        }
    )
    assert social == {case.case_id: None}


def test_v5_records_a_non_null_social_intent_as_a_valid_empty_plan() -> None:
    """A social misclassification is schema-valid (no retry) and shows up as a missed route."""
    case = _case("training_01")
    client = FakeClient([_response({"execute": [], "handoffs": [], "social_intent": "thanks"})])
    attempts, _, social = run_cases([case], client=client)
    assert len(client.models.calls) == 1
    [attempt] = attempts[case.case_id]
    assert attempt.schema_valid is True
    assert attempt.plan is not None and attempt.plan.requests == []
    assert social == {case.case_id: "thanks"}
    section = social_intent_section(social)
    assert "1 / 1" in section and "training_01" in section


def test_v5_mixed_social_and_execute_output_is_schema_invalid_and_retried_once() -> None:
    case = _case("training_01")
    client = FakeClient(
        [
            _response({"execute": ["training"], "handoffs": [], "social_intent": "thanks"}),
            _response({"execute": ["training"], "handoffs": []}),
        ]
    )
    attempts, _, social = run_cases([case], client=client)
    assert len(client.models.calls) == 2
    assert [attempt.schema_valid for attempt in attempts[case.case_id]] == [False, True]
    assert attempts[case.case_id][0].requested_capability_names == ["training"]
    assert social == {case.case_id: None}


def test_v5_retry_policy_is_unchanged_exactly_one_schema_retry() -> None:
    case = _case("training_01")
    client = FakeClient(
        [FakeResponse(text="not-json"), _response({"execute": ["training"], "handoffs": []})]
    )
    attempts, _, _ = run_cases([case], client=client)
    assert len(client.models.calls) == 2
    assert [attempt.schema_valid for attempt in attempts[case.case_id]] == [False, True]


def test_v5_artifacts_carry_the_production_prompt_version_and_frozen_gates() -> None:
    case = _case("training_01")
    client = FakeClient([_response({"execute": ["training"], "handoffs": []})])
    attempts, performance, _ = run_cases([case], client=client)
    records, summary, report = build_artifacts(
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
    assert summary["model"] == "gemini-3.1-flash-lite"
    assert summary["benchmark_id"] == "orchestration-router-v5"
    assert records[0]["model"] == "gemini-3.1-flash-lite"
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
