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
CONFIG_V1_PATH = BENCHMARK_DIR / "benchmark_v1.yaml"

Category = Literal[
    "training_only",
    "life_only",
    "walk_only",
    "multi_execute",
    "pure_handoff",
    "execute_handoff",
    "clarify",
    "boundary_adversarial",
]
PromptVersion = Literal["semantic-router-ko-v1", "semantic-router-ko-v2"]


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
    model: Literal["gemini-3.5-flash-lite"]
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


def load_benchmark_config(path: Path = CONFIG_V1_PATH) -> BenchmarkConfig:
    return BenchmarkConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
