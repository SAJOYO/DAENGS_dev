"""Model-substitution acceptance tests: gemini-3.1-flash-lite vs. frozen v3 contract.

No Gemini calls here (FakeClient only). These assert the ONE changed variable is the
model id, everything else (prompt version, gold set, gates, retry policy) matches v3.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from tools.router_benchmark.evaluate import apply_acceptance_gates, evaluate_benchmark
from tools.router_benchmark.prompt_v3 import PROMPT_VERSION as V3_PROMPT_VERSION
from tools.router_benchmark.prompt_v3 import build_semantic_router_prompt
from tools.router_benchmark.runner import build_artifacts
from tools.router_benchmark.runner_v2 import GENERATION_CONFIG, run_cases
from tools.router_benchmark.runner_v4 import MODEL_ID as V4_MODEL_ID
from tools.router_benchmark.schemas import GoldCase, load_benchmark_config, load_gold_v3_cases
from tools.router_benchmark.semantic_v2 import SemanticRoutingDecision


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


def _decision(*execute: str, handoffs: tuple[str, ...] = ()) -> SemanticRoutingDecision:
    return SemanticRoutingDecision.model_validate(
        {"execute": list(execute), "handoffs": list(handoffs)}
    )


def _response(decision: SemanticRoutingDecision) -> FakeResponse:
    return FakeResponse(
        parsed=decision,
        usage_metadata=SimpleNamespace(
            prompt_token_count=100, candidates_token_count=10, total_token_count=110
        ),
    )


def test_v4_model_id_is_3_1_flash_lite() -> None:
    assert V4_MODEL_ID == "gemini-3.1-flash-lite"


def test_v4_reuses_the_unchanged_v3_prompt_version() -> None:
    assert V3_PROMPT_VERSION == "semantic-router-ko-v3"


def test_v4_uses_all_80_corrected_v3_gold_cases() -> None:
    assert len(load_gold_v3_cases()) == 80


def test_v4_provider_call_uses_gemini_3_1_flash_lite() -> None:
    case = _case("training_01")
    client = FakeClient([_response(_decision("training"))])
    run_cases(
        [case],
        client=client,
        prompt_builder=build_semantic_router_prompt,
        model_id=V4_MODEL_ID,
    )
    assert client.models.calls[0]["model"] == "gemini-3.1-flash-lite"


def test_v4_case_result_records_3_1_flash_lite_not_3_5() -> None:
    case = _case("training_01")
    client = FakeClient([_response(_decision("training"))])
    attempts, performance = run_cases(
        [case],
        client=client,
        prompt_builder=build_semantic_router_prompt,
        model_id=V4_MODEL_ID,
    )
    records, summary, _ = build_artifacts(
        [case],
        attempts,
        performance,
        prompt_version=V3_PROMPT_VERSION,
        generation_config=GENERATION_CONFIG,
        benchmark_id="orchestration-router-v4",
        gold_version="gold-v3-overlay-mixed-09",
        model_id=V4_MODEL_ID,
    )
    assert records[0]["model"] == "gemini-3.1-flash-lite"
    assert summary["model"] == "gemini-3.1-flash-lite"


def test_v4_retry_policy_is_unchanged_exactly_one_schema_retry() -> None:
    case = _case("training_01")
    client = FakeClient([FakeResponse(text="not-json"), _response(_decision("training"))])
    attempts, _ = run_cases(
        [case],
        client=client,
        prompt_builder=build_semantic_router_prompt,
        model_id=V4_MODEL_ID,
    )
    assert len(client.models.calls) == 2
    assert [attempt.schema_valid for attempt in attempts[case.case_id]] == [False, True]


def test_v4_uses_the_same_frozen_acceptance_gates_as_v3() -> None:
    cases = load_gold_v3_cases()
    perfect = evaluate_benchmark(
        cases,
        {case.case_id: [case.gold_route_plan] for case in cases},
        prompt_version=V3_PROMPT_VERSION,
        model_id=V4_MODEL_ID,
    )
    verdict = apply_acceptance_gates(perfect.summary, load_benchmark_config())
    assert verdict.verdict == "PASS"
    assert len(verdict.checks) == 15
    assert perfect.results[0].model == "gemini-3.1-flash-lite"


def test_historical_v3_default_still_reports_3_5_flash_lite() -> None:
    """build_artifacts()/evaluate_benchmark() must default to the unchanged v3 model."""
    case = _case("training_01")
    client = FakeClient([_response(_decision("training"))])
    attempts, performance = run_cases(
        [case], client=client, prompt_builder=build_semantic_router_prompt
    )
    assert client.models.calls[0]["model"] == "gemini-3.5-flash-lite"
    records, summary, _ = build_artifacts(
        [case],
        attempts,
        performance,
        prompt_version=V3_PROMPT_VERSION,
        generation_config=GENERATION_CONFIG,
    )
    assert records[0]["model"] == "gemini-3.5-flash-lite"
    assert summary["model"] == "gemini-3.5-flash-lite"
