"""Focused tests for the single-purpose Phase 2 Gemini runner."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from tools.router_benchmark.runner import build_artifacts, run_cases
from tools.router_benchmark.schemas import GoldCase, load_gold_cases


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


def _case(case_id: str = "training_01") -> GoldCase:
    return next(case for case in load_gold_cases() if case.case_id == case_id)


def _valid_response(case: GoldCase) -> FakeResponse:
    return FakeResponse(
        parsed=case.gold_route_plan,
        usage_metadata=SimpleNamespace(
            prompt_token_count=100,
            candidates_token_count=20,
            total_token_count=120,
        ),
    )


def test_schema_valid_output_is_not_retried() -> None:
    case = _case()
    client = FakeClient([_valid_response(case)])
    attempts, performance = run_cases([case], client=client)
    assert len(client.models.calls) == 1
    assert len(attempts[case.case_id]) == 1
    assert attempts[case.case_id][0].schema_valid is True
    assert performance[case.case_id].total_tokens == 120


def test_schema_invalid_first_attempt_is_retried_exactly_once() -> None:
    case = _case()
    client = FakeClient([FakeResponse(text="not-json"), _valid_response(case)])
    attempts, _ = run_cases([case], client=client)
    assert len(client.models.calls) == 2
    assert [attempt.schema_valid for attempt in attempts[case.case_id]] == [False, True]


def test_second_invalid_attempt_remains_failed() -> None:
    case = _case()
    client = FakeClient([FakeResponse(text="not-json"), FakeResponse(text="still-not-json")])
    attempts, performance = run_cases([case], client=client)
    records, summary, _ = build_artifacts([case], attempts, performance)
    assert len(client.models.calls) == 2
    assert records[0]["final_schema_valid"] is False
    assert records[0]["failure_bucket"] == "SCHEMA_FAILURE"
    assert summary["metrics"]["unrecovered_schema_failure_count"] == 1


def test_valid_semantic_misroute_is_not_retried() -> None:
    case = _case()
    wrong = _case("life_01").gold_route_plan
    client = FakeClient([FakeResponse(parsed=wrong)])
    attempts, performance = run_cases([case], client=client)
    records, _, _ = build_artifacts([case], attempts, performance)
    assert len(client.models.calls) == 1
    assert records[0]["exact_match"] is False


def test_runner_only_calls_gemini_and_reuses_frozen_evaluator(monkeypatch) -> None:
    case = _case()
    client = FakeClient([_valid_response(case)])
    called = False

    def fake_evaluate(cases, attempts, performance, **kwargs):
        nonlocal called
        called = True
        from tools.router_benchmark.evaluate import evaluate_benchmark

        return evaluate_benchmark(cases, attempts, performance, **kwargs)

    monkeypatch.setattr("tools.router_benchmark.runner.evaluate_benchmark", fake_evaluate)
    attempts, performance = run_cases([case], client=client)
    build_artifacts([case], attempts, performance)
    assert called is True
    assert set(client.models.calls[0]) == {"model", "contents", "config"}
