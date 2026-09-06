"""Schemas and loaders for the frozen offline router benchmark."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from daengs_backend.orchestration.contracts import RoutePlan

BENCHMARK_DIR = Path(__file__).resolve().parents[2] / "evals" / "orchestration_router"
GOLD_V1_PATH = BENCHMARK_DIR / "gold_v1.jsonl"
GOLD_V3_CORRECTIONS_PATH = BENCHMARK_DIR / "gold_v3_corrections.json"
CONFIG_V1_PATH = BENCHMARK_DIR / "benchmark_v1.yaml"
# PR #204 — the Place acceptance set. Deliberately a SEPARATE file: the 80-case v1 gold
# is the frozen regression baseline and gains no cases, so a Place miss and a v6
# regression can never be confused for one another.
GOLD_PLACE_V1_PATH = BENCHMARK_DIR / "gold_place_v1.jsonl"

Category = Literal[
    "training_only",
    "life_only",
    "walk_only",
    "multi_execute",
    "pure_handoff",
    "execute_handoff",
    "clarify",
    "boundary_adversarial",
    # PR #204 — used only by the Place acceptance set (gold_place_v1.jsonl). The frozen
    # 80-case v1 gold has no case in this category and is not re-annotated.
    "place_only",
]
PromptVersion = Literal[
    "semantic-router-ko-v1",
    "semantic-router-ko-v2",
    "semantic-router-ko-v3",
    "semantic-router-ko-v4",
    "semantic-router-ko-v5",  # PR #172 — production Life/unsupported-care boundary (runner_v6)
    "semantic-router-ko-v6",  # PR #172 — routine-care vs today's walking window (runner_v7)
    "semantic-router-ko-v7",  # PR #204 — the `place` destination (runner_v8)
    "semantic-router-ko-v8",  # PR #279 — explicit-exclusion (negation) sentence (runner_v9)
    "semantic-router-ko-v9",  # PR #279 / D-056 — `general` as an additive destination (runner_v10)
    # PR #252 — the LangChain agent orchestrator (D-055). Not a semantic-router prompt
    # version at all: the agent has no routing prompt, it picks tools in a loop. It lives
    # in the same Literal because `CaseResult.prompt_version` is what tells two rows of
    # `evaluate_benchmark` output apart, and the comparison feeds both implementations
    # through that one scorer on purpose (card ①).
    #
    # ⚠ Adding a value is safe for the frozen runners; changing or removing one is not
    # (card note ④) — `runner_v5`~`v8` import this module to reproduce their reports.
    "agent-ko-v1",
    # PR #272 — the agent aligned with the frozen CLARIFY contract (tools record a
    # selection; the shared planner gates and the shared engine executes). Added, never
    # substituted: `agent-ko-v1` stays so `comparison_v1_*` remains reproducible.
    "agent-ko-v2",
    # PR #279 — the agent prompt mirrors v8's exclusion sentence and hands "no tool
    # applies" to the planner's general fallback (D-055 ⑦ rule 1). `agent-ko-v2` stays
    # so `comparison_v2_*` remains reproducible.
    "agent-ko-v3",
    # PR #279 / D-056 — `answer_generally` mirrors the router's v9 `general` destination.
    "agent-ko-v4",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GoldCase(StrictModel):
    case_id: str = Field(pattern=r"^[a-z][a-z0-9_]+$")
    category: Category
    query: str = Field(min_length=1, max_length=1_000)
    context: dict[str, Any]
    gold_route_plan: RoutePlan
    rationale: str = Field(min_length=1)


class RetryPolicy(StrictModel):
    max_attempts: Literal[2]
    retry_on: Literal["schema_validation_error"]
    retry_semantic_misroute: Literal[False]


class PerformanceFields(StrictModel):
    latency_ms: bool
    warm_p50: bool
    warm_p95: bool
    input_tokens: bool
    output_tokens: bool
    total_tokens: bool
    acceptance_gate: Literal[False]


class BenchmarkConfig(StrictModel):
    benchmark_id: Literal["orchestration-router-v1"]
    phase: Literal[1]
    execution_authorized: Literal[False]
    prompt_version: Literal["semantic-router-ko-v1"]
    model: Literal["gemini-3.5-flash-lite"]
    locale: Literal["ko-KR"]
    scored_case_count: Literal[80]
    retry: RetryPolicy
    metrics: list[str]
    acceptance_gates: dict[str, float | int]
    performance: PerformanceFields

    @model_validator(mode="after")
    def only_frozen_model(self) -> BenchmarkConfig:
        if self.model != "gemini-3.5-flash-lite":
            raise ValueError("Card 2A v1 has exactly one fixed model")
        return self


class AttemptValidation(StrictModel):
    schema_valid: bool
    plan: RoutePlan | None = None
    requested_capability_names: list[str] = Field(default_factory=list)
    handoff_target_names: list[str] = Field(default_factory=list)
    error_category: str | None = None

    @model_validator(mode="after")
    def validity_matches_plan(self) -> AttemptValidation:
        if self.schema_valid != (self.plan is not None):
            raise ValueError("schema_valid must match presence of a validated RoutePlan")
        return self


class PerformanceObservation(StrictModel):
    latency_ms: float | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class CaseResult(StrictModel):
    case_id: str
    prompt_version: PromptVersion
    model: Literal["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
    attempt_count: int = Field(ge=1, le=2)
    first_pass_schema_valid: bool
    final_schema_valid: bool
    retry_used: bool
    prediction: RoutePlan | None
    exact_match: bool
    execute_precision: float = Field(ge=0, le=1)
    execute_recall: float = Field(ge=0, le=1)
    handoff_precision: float = Field(ge=0, le=1)
    handoff_recall: float = Field(ge=0, le=1)
    clarify_gold: bool
    clarify_predicted: bool
    latency_ms: float | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    error_category: str | None = None


class BenchmarkSummary(StrictModel):
    case_count: int
    first_pass_schema_valid_rate: float
    retry_recovery_count: int
    final_schema_valid_rate: float
    unrecovered_schema_failure_count: int
    exact_route_plan_match: float
    executable_precision: float
    executable_recall: float
    training_precision: float
    training_recall: float
    life_precision: float
    life_recall: float
    walk_precision: float
    walk_recall: float
    multi_execute_recall: float
    exact_executable_set_accuracy_multi: float
    handoff_precision: float
    handoff_recall: float
    skin_handoff_recall: float
    gait_handoff_recall: float
    exact_mixed_execute_handoff_match: float
    clarify_precision: float
    clarify_recall: float
    false_positive_clarify_rate: float
    forbidden_execute_count: int
    invented_unsupported_capability_count: int


class BenchmarkPerformanceSummary(StrictModel):
    warm_sample_count: int = Field(ge=0)
    warm_p50_ms: float | None = Field(default=None, ge=0)
    warm_p95_ms: float | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class BenchmarkEvaluation(StrictModel):
    results: list[CaseResult]
    summary: BenchmarkSummary
    performance: BenchmarkPerformanceSummary


class GateCheck(StrictModel):
    metric: str
    operator: Literal[">=", "<="]
    threshold: float
    actual: float
    passed: bool


class AcceptanceVerdict(StrictModel):
    verdict: Literal["PASS", "FAIL"]
    checks: list[GateCheck]


def load_gold_cases(path: Path = GOLD_V1_PATH) -> list[GoldCase]:
    cases: list[GoldCase] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
        cases.append(GoldCase.model_validate(raw))
    return cases


def load_gold_place_cases() -> list[GoldCase]:
    """The frozen Place acceptance set (PR #204), loaded by the same GoldCase schema."""
    return load_gold_cases(GOLD_PLACE_V1_PATH)


def load_gold_v3_cases() -> list[GoldCase]:
    """Apply the one human-confirmed annotation correction without overwriting v1 gold."""
    document = json.loads(GOLD_V3_CORRECTIONS_PATH.read_text(encoding="utf-8"))
    if document.get("base_gold") != GOLD_V1_PATH.name:
        raise ValueError("v3 correction overlay must reference gold_v1.jsonl")
    corrections = document.get("annotation_corrections")
    if not isinstance(corrections, list) or len(corrections) != 1:
        raise ValueError("v3 must contain exactly one reviewed annotation correction")
    correction = corrections[0]
    if correction.get("case_id") != "mixed_09":
        raise ValueError("the only approved v3 correction is mixed_09")

    cases = load_gold_cases()
    for index, case in enumerate(cases):
        if case.case_id != "mixed_09":
            continue
        if correction.get("query") != case.query:
            raise ValueError("mixed_09 correction query does not match frozen v1")
        cases[index] = case.model_copy(
            update={
                "gold_route_plan": RoutePlan.model_validate(correction["gold_route_plan"]),
                "rationale": correction["rationale"],
            }
        )
        return cases
    raise ValueError("mixed_09 is missing from frozen v1 gold")


def load_benchmark_config(path: Path = CONFIG_V1_PATH) -> BenchmarkConfig:
    return BenchmarkConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
