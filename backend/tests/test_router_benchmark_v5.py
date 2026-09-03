"""v5 prompt-regression runner: production `semantic-router-ko-v4` vs. the frozen v3 contract.

No Gemini calls here (FakeClient only). The ONE changed variable is the production
prompt/schema (v4 adds an exclusive `social_intent`); model id, gold set, gates, and
the single-schema-retry policy must match the accepted v4 run byte-for-byte.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from daengs_backend.orchestration.semantic import PROMPT_VERSION, SemanticRoutingDecision
from tools.router_benchmark.evaluate import apply_acceptance_gates, evaluate_benchmark
from tools.router_benchmark.runner import build_artifacts
from tools.router_benchmark.runner_v2 import GENERATION_CONFIG
from tools.router_benchmark.runner_v4 import MODEL_ID as V4_MODEL_ID
from tools.router_benchmark.runner_v5 import (
    BENCHMARK_ID,
    GOLD_VERSION,
    MODEL_ID,
    run_cases,
    social_intent_section,
)
from tools.router_benchmark.schemas import GoldCase, load_benchmark_config, load_gold_v3_cases


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
    assert PROMPT_VERSION == "semantic-router-ko-v4"
    assert GOLD_VERSION == "gold-v3-overlay-mixed-09"
    assert BENCHMARK_ID == "orchestration-router-v5"
    assert len(load_gold_v3_cases()) == 80


def test_v5_provider_call_uses_the_production_prompt_and_schema() -> None:
    case = _case("training_01")
    client = FakeClient([_response({"execute": ["training"], "handoffs": []})])
    attempts, _, social = run_cases([case], client=client)
    call = client.models.calls[0]
    assert call["model"] == "gemini-3.1-flash-lite"
    assert "PROMPT_VERSION: semantic-router-ko-v4" in call["contents"]
    assert "social_intent" in call["contents"]
    assert call["config"].response_json_schema == SemanticRoutingDecision.model_json_schema()
    assert attempts[case.case_id][0].schema_valid is True
    assert attempts[case.case_id][0].plan == case.gold_route_plan.model_copy(
        update={"router": "llm", "model": "gemini-3.1-flash-lite"}
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


def test_v5_artifacts_carry_the_v4_prompt_version_and_frozen_gates() -> None:
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
    assert summary["prompt_version"] == "semantic-router-ko-v4"
    assert summary["model"] == "gemini-3.1-flash-lite"
    assert summary["benchmark_id"] == "orchestration-router-v5"
    assert records[0]["model"] == "gemini-3.1-flash-lite"
    assert "semantic-router-ko-v4" in report

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
