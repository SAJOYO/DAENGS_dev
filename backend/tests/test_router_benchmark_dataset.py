"""Integrity checks for the human-frozen semantic-router gold set."""

from __future__ import annotations

import json
import re
from collections import Counter
from difflib import SequenceMatcher

from daengs_backend.orchestration.contracts import RoutePlan
from daengs_evals.router_benchmark.schemas import (
    BENCHMARK_DIR,
    load_benchmark_config,
    load_gold_cases,
)

EXPECTED_COUNTS = {
    "training_only": 10,
    "life_only": 10,
    "walk_only": 10,
    "multi_execute": 12,
    "pure_handoff": 10,
    "execute_handoff": 10,
    "clarify": 12,
    "boundary_adversarial": 6,
}
ALLOWED_EXECUTE = {"training", "life", "walk"}
ALLOWED_HANDOFFS = {"skin", "gait"}


def test_exactly_80_unique_scored_cases_with_frozen_distribution() -> None:
    cases = load_gold_cases()
    assert len(cases) == 80
    assert len({case.case_id for case in cases}) == 80
    assert Counter(case.category for case in cases) == EXPECTED_COUNTS


def test_every_gold_plan_is_the_real_card_1_route_plan() -> None:
    for case in load_gold_cases():
        assert isinstance(case.gold_route_plan, RoutePlan)
        RoutePlan.model_validate(case.gold_route_plan.model_dump(mode="json"))


def test_gold_taxonomy_and_clarify_exclusivity() -> None:
    for case in load_gold_cases():
        plan = case.gold_route_plan
        request_names = {request.capability.value for request in plan.requests}
        handoff_names = {handoff.target for handoff in plan.handoffs}
        assert request_names <= ALLOWED_EXECUTE
        assert not request_names & ALLOWED_HANDOFFS
        assert handoff_names <= ALLOWED_HANDOFFS
        if plan.clarify is not None:
            assert plan.requests == []
            assert plan.handoffs == []


def test_mixed_execute_and_handoff_cases_really_contain_both() -> None:
    mixed = [case for case in load_gold_cases() if case.category == "execute_handoff"]
    assert len(mixed) == 10
    assert all(case.gold_route_plan.requests and case.gold_route_plan.handoffs for case in mixed)


def test_walk_execution_always_has_valid_structured_coordinates() -> None:
    for case in load_gold_cases():
        walk_requests = [
            request
            for request in case.gold_route_plan.requests
            if request.capability.value == "walk"
        ]
        for request in walk_requests:
            location = case.context.get("location")
            assert isinstance(location, dict)
            assert request.payload.lat == location["lat"]
            assert request.payload.lon == location["lon"]


def test_context_has_no_private_or_evaluator_fields() -> None:
    forbidden = {
        "authorization",
        "jwt",
        "jwe",
        "access_token",
        "refresh_token",
        "cookie",
        "category",
        "gold_route_plan",
        "rationale",
        "expected_capability",
        "metrics",
    }

    def keys(value: object) -> set[str]:
        if isinstance(value, dict):
            return {str(key).lower() for key in value} | {
                nested for item in value.values() for nested in keys(item)
            }
        if isinstance(value, list):
            return {nested for item in value for nested in keys(item)}
        return set()

    for case in load_gold_cases():
        assert not keys(case.context) & forbidden


def test_simple_near_duplicate_audit_has_no_trivial_paraphrase_pairs() -> None:
    """Normalize whitespace/punctuation, then flag suspiciously similar authored queries."""
    cases = load_gold_cases()

    def normalize(text: str) -> str:
        return re.sub(r"[^0-9a-z가-힣]", "", text.lower())

    suspicious = []
    for index, left in enumerate(cases):
        for right in cases[index + 1 :]:
            ratio = SequenceMatcher(None, normalize(left.query), normalize(right.query)).ratio()
            if ratio >= 0.88:
                suspicious.append((left.case_id, right.case_id, ratio))
    assert suspicious == []


def test_config_freezes_one_model_prompt_retry_and_numeric_gates() -> None:
    config = load_benchmark_config()
    assert config.execution_authorized is False
    assert config.model == "gemini-3.5-flash-lite"
    assert config.prompt_version == "semantic-router-ko-v1"
    assert config.retry.max_attempts == 2
    assert config.retry.retry_on == "schema_validation_error"
    assert config.retry.retry_semantic_misroute is False
    assert config.acceptance_gates == {
        "final_schema_valid_rate": 1.0,
        "first_pass_schema_valid_rate": 0.975,
        "forbidden_execute_count": 0,
        "invented_unsupported_capability_count": 0,
        "exact_route_plan_match": 0.90,
        "executable_precision": 0.95,
        "executable_recall": 0.95,
        "multi_execute_recall": 0.90,
        "exact_executable_set_accuracy_multi": 0.90,
        "skin_handoff_recall": 1.0,
        "gait_handoff_recall": 1.0,
        "handoff_precision": 0.95,
        "exact_mixed_execute_handoff_match": 0.90,
        "clarify_precision": 0.90,
        "clarify_recall": 0.90,
    }


def test_benchmark_directory_contains_no_winner_or_dashboard_artifacts() -> None:
    names = {path.name for path in BENCHMARK_DIR.iterdir()}
    assert not any("winner" in name or name.endswith(".html") for name in names)


def test_phase_2_result_artifacts_parse_and_cover_the_frozen_set() -> None:
    records = [
        json.loads(line)
        for line in (BENCHMARK_DIR / "results_v1.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    summary = json.loads((BENCHMARK_DIR / "summary_v1.json").read_text(encoding="utf-8"))
    assert len(records) == 80
    assert {record["case_id"] for record in records} == {case.case_id for case in load_gold_cases()}
    assert summary["scored_cases"] == 80
    assert summary["verdict"] in {"PASS", "FAIL"}
