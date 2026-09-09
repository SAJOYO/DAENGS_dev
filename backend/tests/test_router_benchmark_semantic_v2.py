"""Ownership-boundary and retry tests for semantic-only router v2."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from daengs_evals.router_benchmark.prompt_v2 import build_semantic_router_prompt
from daengs_evals.router_benchmark.runner import build_artifacts
from daengs_evals.router_benchmark.runner_v2 import GENERATION_CONFIG, run_cases
from daengs_evals.router_benchmark.schemas import GoldCase, load_gold_cases
from daengs_evals.router_benchmark.semantic_v2 import (
    SemanticRoutingDecision,
    assemble_route_plan,
    validate_semantic_decision,
)


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
    return next(case for case in load_gold_cases() if case.case_id == case_id)


def _decision(*execute: str, handoffs: tuple[str, ...] = ()) -> SemanticRoutingDecision:
    return SemanticRoutingDecision.model_validate(
        {"execute": list(execute), "handoffs": list(handoffs)}
    )


def _response(decision: SemanticRoutingDecision) -> FakeResponse:
    return FakeResponse(
        parsed=decision,
        usage_metadata=SimpleNamespace(
            prompt_token_count=100,
            candidates_token_count=10,
            total_token_count=110,
        ),
    )


def test_semantic_schema_rejects_gemini_owned_question_text() -> None:
    result = validate_semantic_decision(
        {"execute": ["training"], "handoffs": [], "question": "rewritten"}
    )
    assert result.schema_valid is False


def test_training_and_life_receive_original_query_byte_for_byte() -> None:
    query = "원문  그대로 — 반려견 질문?"
    plan = assemble_route_plan(_decision("training", "life"), query=query, context={})
    assert [request.payload.question.encode() for request in plan.requests] == [
        query.encode(),
        query.encode(),
    ]


def test_semantic_schema_rejects_gemini_owned_coordinates() -> None:
    result = validate_semantic_decision(
        {"execute": ["walk"], "handoffs": [], "lat": 37.5, "lon": 127.0}
    )
    assert result.schema_valid is False


def test_semantic_schema_rejects_duplicate_or_invented_capabilities() -> None:
    duplicate = validate_semantic_decision({"execute": ["training", "training"], "handoffs": []})
    invented = validate_semantic_decision({"execute": ["place"], "handoffs": []})
    assert duplicate.schema_valid is False
    assert invented.schema_valid is False


def test_semantic_prompt_does_not_receive_structured_coordinates() -> None:
    prompt = build_semantic_router_prompt(
        query="오늘 산책 어때?",
        context={"location": {"lat": 37.5665, "lon": 126.978}, "source": "chat"},
    )
    assert "37.5665" not in prompt
    assert "126.978" not in prompt


def test_walk_coordinates_come_only_from_structured_context() -> None:
    plan = assemble_route_plan(
        _decision("walk"),
        query="오늘 걸어도 돼?",
        context={"location": {"lat": 35.1796, "lon": 129.0756}},
    )
    assert plan.requests[0].payload.model_dump() == {"lat": 35.1796, "lon": 129.0756}


def test_missing_lat_produces_exclusive_clarify() -> None:
    plan = assemble_route_plan(
        _decision("walk", "training", handoffs=("skin",)),
        query="둘 다 해줘",
        context={"location": {"lon": 126.978}},
    )
    assert plan.clarify is not None and plan.clarify.missing == ["location.lat"]
    assert plan.requests == [] and plan.handoffs == []


def test_missing_lon_produces_clarify() -> None:
    plan = assemble_route_plan(
        _decision("walk"), query="산책?", context={"location": {"lat": 37.5665}}
    )
    assert plan.clarify is not None and plan.clarify.missing == ["location.lon"]


def test_both_missing_coordinates_are_derived_by_code() -> None:
    plan = assemble_route_plan(_decision("walk"), query="산책?", context={})
    assert plan.clarify is not None
    assert plan.clarify.missing == ["location.lat", "location.lon"]


def test_natural_language_place_name_cannot_create_coordinates() -> None:
    plan = assemble_route_plan(_decision("walk"), query="부산 해운대에서 산책 어때?", context={})
    assert plan.requests == []
    assert plan.clarify is not None


def test_skin_reason_is_owned_by_code() -> None:
    plan = assemble_route_plan(_decision(handoffs=("skin",)), query="피부 봐줘", context={})
    assert plan.handoffs[0].model_dump() == {
        "target": "skin",
        "reason": "image_upload_required",
    }


def test_gait_reason_is_owned_by_code() -> None:
    plan = assemble_route_plan(_decision(handoffs=("gait",)), query="보행 봐줘", context={})
    assert plan.handoffs[0].model_dump() == {
        "target": "gait",
        "reason": "video_upload_required",
    }


def test_multi_execute_survives_assembly() -> None:
    plan = assemble_route_plan(
        _decision("training", "life", "walk"),
        query="세 가지 질문",
        context={"location": {"lat": 37.5, "lon": 127.0}},
    )
    assert [request.capability.value for request in plan.requests] == [
        "training",
        "life",
        "walk",
    ]


def test_execute_and_handoff_survive_assembly() -> None:
    plan = assemble_route_plan(_decision("training", handoffs=("gait",)), query="둘 다", context={})
    assert [request.capability.value for request in plan.requests] == ["training"]
    assert [handoff.target for handoff in plan.handoffs] == ["gait"]


def test_valid_semantic_misroute_is_not_retried() -> None:
    case = _case("training_01")
    client = FakeClient([_response(_decision("life"))])
    attempts, _ = run_cases([case], client=client)
    assert len(client.models.calls) == 1
    assert attempts[case.case_id][0].schema_valid is True
    assert attempts[case.case_id][0].plan.requests[0].capability.value == "life"


def test_invalid_semantic_decision_gets_exactly_one_retry() -> None:
    case = _case("training_01")
    client = FakeClient([FakeResponse(text="not-json"), _response(_decision("training"))])
    attempts, _ = run_cases([case], client=client)
    assert len(client.models.calls) == 2
    assert [attempt.schema_valid for attempt in attempts[case.case_id]] == [False, True]


def test_second_invalid_semantic_decision_remains_failed() -> None:
    case = _case("training_01")
    client = FakeClient([FakeResponse(text="not-json"), FakeResponse(text="still-not-json")])
    attempts, _ = run_cases([case], client=client)
    assert len(client.models.calls) == 2
    assert attempts[case.case_id][-1].schema_valid is False


def test_final_evaluator_compares_assembled_card_1_route_plan() -> None:
    case = _case("training_01")
    client = FakeClient([_response(_decision("training"))])
    attempts, performance = run_cases([case], client=client)
    records, _, _ = build_artifacts(
        [case],
        attempts,
        performance,
        prompt_version="semantic-router-ko-v2",
        generation_config=GENERATION_CONFIG,
    )
    assert records[0]["prediction"] == case.gold_route_plan.model_dump(mode="json")
    assert records[0]["exact_match"] is True
