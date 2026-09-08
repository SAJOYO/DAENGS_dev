"""비교 v2 러너 — 순서 균형 · 통제 설정 · 지표 집계 (#272). 모델도 네트워크도 없다."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("langchain")

from pydantic import SecretStr

from daengs_backend.config import settings
from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    AssistantStatus,
    RoutePlan,
    RouterKind,
)
from daengs_backend.orchestration.planner import assemble_route_plan
from daengs_backend.orchestration.semantic import (
    ROUTER_MODEL_ID,
    SemanticRoutingDecision,
    router_generation_config,
)
from daengs_evals.orchestrator_comparison import runner_v2 as v2
from daengs_evals.orchestrator_comparison.runner import Meter
from daengs_evals.router_benchmark.schemas import (
    AttemptValidation,
    GoldCase,
    PerformanceObservation,
    load_gold_cases,
)

CASES = load_gold_cases()


# ── 실행 순서 균형 ────────────────────────────────────────────────────────


def test_first_runner_alternates_by_index_and_inverts_per_run() -> None:
    assert v2.first_runner(1, 0) == "langgraph"
    assert v2.first_runner(1, 1) == "agent"
    assert v2.first_runner(2, 0) == "agent"
    assert v2.first_runner(3, 0) == "langgraph"
    for index in range(len(CASES)):
        assert v2.first_runner(1, index) != v2.first_runner(2, index)
        assert v2.first_runner(1, index) == v2.first_runner(3, index)
        first, second = v2.execution_order(1, index)
        assert {first, second} == set(v2.IMPLEMENTATIONS) and first != second


def test_each_implementation_runs_first_for_half_of_the_cases() -> None:
    for run in v2.RUN_NUMBERS:
        counts = Counter(v2.first_runner(run, i) for i in range(len(CASES)))
        assert counts == {"langgraph": 40, "agent": 40}


@dataclass
class FakeOrchestrator:
    name: str
    log: list[tuple[str, str]]
    sink: dict
    plan_for: dict[str, RoutePlan | None]

    async def run(self, *, query, principal, context, **kwargs) -> AssistantResponse:
        case_id = next(c.case_id for c in CASES if c.query == query)
        self.log.append((self.name, case_id))
        self.sink["plan"] = self.plan_for.get(case_id)
        return AssistantResponse(request_id="r", status=AssistantStatus.ANSWERED, message="x")


def _gold_plans() -> dict[str, RoutePlan]:
    return {c.case_id: c.gold_route_plan for c in CASES}


async def test_repetition_runs_sequentially_in_the_recorded_order() -> None:
    cases = CASES[:5]
    log: list[tuple[str, str]] = []
    sink: dict = {"plan": None}
    pair = {n: FakeOrchestrator(n, log, sink, _gold_plans()) for n in v2.IMPLEMENTATIONS}
    meters = {n: Meter() for n in v2.IMPLEMENTATIONS}

    runs = await v2.run_repetition(cases, 2, pair, meters, sink, log=lambda _: None)

    expected = [
        (name, case.case_id)
        for index, case in enumerate(cases)
        for name in v2.execution_order(2, index)
    ]
    assert log == expected  # 순차이고, 기록된 순서가 실제 순서다
    for name in v2.IMPLEMENTATIONS:
        assert [r.case_id for r in runs[name]] == [c.case_id for c in cases]
        for index, result in enumerate(runs[name]):
            assert result.first_runner == v2.first_runner(2, index)
            assert result.position == (1 if result.first_runner == name else 2)
            assert result.attempt.schema_valid and result.run == 2


async def test_warm_up_is_one_call_per_implementation_and_not_scored() -> None:
    log: list[tuple[str, str]] = []
    sink: dict = {"plan": None}
    pair = {n: FakeOrchestrator(n, log, sink, _gold_plans()) for n in v2.IMPLEMENTATIONS}
    meters = {n: Meter() for n in v2.IMPLEMENTATIONS}

    record = await v2.warm_up(pair, meters, sink, CASES[0], run=1)
    assert Counter(name for name, _ in log) == {"langgraph": 1, "agent": 1}
    assert record["case_id"] == CASES[0].case_id and set(record["results"]) == set(
        v2.IMPLEMENTATIONS
    )

    runs = await v2.run_repetition(CASES[:2], 1, pair, meters, sink, log=lambda _: None)
    assert all(len(runs[n]) == 2 for n in v2.IMPLEMENTATIONS)  # 예열은 결과에 없다


# ── 통제 설정 ─────────────────────────────────────────────────────────────


class FakeModels:
    def __init__(self, parsed) -> None:
        self.parsed = parsed
        self.calls: list[dict] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            parsed=self.parsed,
            text=None,
            usage_metadata=SimpleNamespace(prompt_token_count=100, candidates_token_count=7),
        )


async def test_langgraph_transport_uses_the_production_generation_config() -> None:
    meter = Meter()
    client = SimpleNamespace(models=FakeModels(SemanticRoutingDecision(execute=["training"])))
    generate = v2._metered_semantic_generate(meter, client=client)
    result = await generate("prompt")

    assert isinstance(result, SemanticRoutingDecision)
    call = client.models.calls[0]
    assert call["model"] == ROUTER_MODEL_ID
    expected = router_generation_config()
    assert call["config"].temperature == expected.temperature == 0.0
    assert call["config"].candidate_count == expected.candidate_count == 1
    assert call["config"].max_output_tokens == expected.max_output_tokens == 256
    assert call["config"].response_mime_type == "application/json"
    assert call["config"].response_json_schema == expected.response_json_schema
    assert (meter.turns, meter.input_tokens, meter.output_tokens) == (1, 100, 7)


def test_build_pair_uses_the_runtime_agent_model_with_controlled_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langchain_google_genai import ChatGoogleGenerativeAI

    monkeypatch.setattr(settings, "gemini_api_key", SecretStr("test-key"))
    meters = {n: Meter() for n in v2.IMPLEMENTATIONS}
    sink: dict = {"plan": None}
    pair = v2.build_pair(meters, sink)

    model = pair["agent"]._model
    assert isinstance(model, ChatGoogleGenerativeAI)
    assert ROUTER_MODEL_ID in model.model
    assert model.temperature == 0.0 and "temperature" in model.model_fields_set
    assert model.n == 1 and model.max_output_tokens == 256 and model.max_retries == 0
    assert model.timeout == settings.gemini_timeout_ms / 1_000
    assert model.callbacks and type(model.callbacks[0]).__name__ == "UsageMeter"
    # 두 구현이 같은 종류의 엔진(계획을 잡는 엔진)과 같은 가짜 어댑터 셋을 문다
    assert isinstance(pair["langgraph"]._engine, v2.RecordingEngine)
    assert isinstance(pair["agent"]._engine, v2.RecordingEngine)
    assert pair["langgraph"]._engine._adapters.keys() == pair["agent"]._engine._adapters.keys()


def test_controlled_settings_come_from_code_constants_and_match() -> None:
    s = v2.controlled_settings()
    for key in (
        "model_id",
        "temperature",
        "candidate_count",
        "max_output_tokens_per_call",
        "provider_timeout_ms",
    ):
        assert s[key]["langgraph"] == s[key]["agent"], key
    assert s["model_id"]["langgraph"] == ROUTER_MODEL_ID
    assert s["temperature"]["langgraph"] == 0.0
    # 두 프롬프트는 #279 에서 같은 PR 안에 함께 올라갔다 (D-055 ⑦ 규칙 1). 기록된
    # `comparison_v2_*` 는 v7/v2 로 남아 있고, 이 값은 **지금** 러너가 보낼 버전이다.
    assert s["prompt_version"] == {"langgraph": "semantic-router-ko-v10", "agent": "agent-ko-v4"}
    assert "never printed" in s["credential"]


# ── 지표 집계 ─────────────────────────────────────────────────────────────


def _plan(case: GoldCase, execute: list[str], handoffs: list[str] = ()) -> RoutePlan:
    return assemble_route_plan(
        SemanticRoutingDecision(execute=execute, handoffs=list(handoffs)),
        query=case.query,
        context=dict(case.context),
        router=RouterKind.LLM,
    )


def _case_run(
    case: GoldCase,
    name: str,
    run: int,
    plan: RoutePlan | None,
    *,
    turns=1,
    tokens=(100, 10),
    latency=500.0,
    index=0,
) -> v2.CaseRun:
    return v2.CaseRun(
        case_id=case.case_id,
        implementation=name,
        run=run,
        first_runner=v2.first_runner(run, index),
        position=1 if v2.first_runner(run, index) == name else 2,
        attempt=AttemptValidation(schema_valid=plan is not None, plan=plan),
        performance=PerformanceObservation(
            latency_ms=latency,
            input_tokens=tokens[0],
            output_tokens=tokens[1],
            total_tokens=sum(tokens),
        ),
        turns=turns,
        status="ANSWERED" if plan is not None else "FAILED",
    )


def _synthetic_runs() -> list[tuple[dict, dict[str, list[v2.CaseRun]]]]:
    """langgraph 는 늘 골드. agent 는 세 곳에서 다르다:
    - clarify_07: 골드는 CLARIFY 인데 훈련만 실행 (조용한 의도 손실, 3회 모두)
    - mixed_01: 반복 2 에서만 핸드오프를 빼먹음 (불안정 + 갈림)
    - life_01: 매번 계획 없음 (FAILED — 조용하지 않음)
    """
    by_id = {c.case_id: c for c in CASES}
    settings_snapshot = v2.controlled_settings()
    loaded = []
    for run in v2.RUN_NUMBERS:
        runs: dict[str, list[v2.CaseRun]] = {n: [] for n in v2.IMPLEMENTATIONS}
        for index, case in enumerate(CASES):
            runs["langgraph"].append(
                _case_run(case, "langgraph", run, case.gold_route_plan, index=index)
            )
            plan: RoutePlan | None = case.gold_route_plan
            if case.case_id == "clarify_07":
                plan = _plan(by_id["clarify_07"], ["training"])
            elif case.case_id == "mixed_01" and run == 2:
                plan = _plan(case, [r.capability.value for r in case.gold_route_plan.requests])
            elif case.case_id == "life_01":
                plan = None
            runs["agent"].append(
                _case_run(
                    case, "agent", run, plan, turns=2, tokens=(200, 20), latency=900.0, index=index
                )
            )
        meta = {"run": run, "source_sha": "abc", "settings": settings_snapshot, "warmup": {}}
        loaded.append((meta, runs))
    return loaded


def test_silent_intent_loss_definition() -> None:
    by_id = {c.case_id: c for c in CASES}
    clarify_07, mixed_01, training_01 = by_id["clarify_07"], by_id["mixed_01"], by_id["training_01"]
    assert v2.silent_intent_loss(clarify_07, _plan(clarify_07, ["training"]))  # 골드 CLARIFY 우회
    assert not v2.silent_intent_loss(clarify_07, clarify_07.gold_route_plan)  # CLARIFY 냄
    assert not v2.silent_intent_loss(clarify_07, None)  # FAILED — 보인다
    assert mixed_01.gold_route_plan.handoffs, "mixed_01 은 핸드오프가 있는 케이스여야 한다"
    dropped = _plan(mixed_01, [r.capability.value for r in mixed_01.gold_route_plan.requests])
    assert v2.silent_intent_loss(mixed_01, dropped)  # 핸드오프만 빠짐
    assert not v2.silent_intent_loss(training_01, training_01.gold_route_plan)
    assert not v2.silent_intent_loss(
        training_01, _plan(training_01, ["training", "life"])
    )  # 더 냄은 손실이 아니다


def test_summary_reports_runs_separately_and_aggregates() -> None:
    summary = v2.build_summary(CASES, _synthetic_runs())

    assert summary["run_count"] == 3 and summary["benchmark_source_sha"] == "abc"
    for r in summary["runs"]:
        assert r["first_runner_balance"] == {"langgraph": 40, "agent": 40}
        lg, ag = r["implementations"]["langgraph"], r["implementations"]["agent"]
        assert lg["summary"]["exact_route_plan_match"] == 1.0
        assert lg["silent_intent_loss_case_ids"] == []
        assert ag["silent_intent_loss_case_ids"] == (
            ["clarify_07", "mixed_01"] if r["run"] == 2 else ["clarify_07"]
        )
        assert ag["summary"]["final_schema_valid_rate"] == pytest.approx(79 / 80)
        assert ag["tokens"]["total"] == 80 * 220 and lg["tokens"]["total"] == 80 * 110
        assert ag["llm_turns"]["total"] == 160 and lg["llm_turns"]["total"] == 80
        assert ag["latency_ms"]["p95"] == 900.0 and lg["latency_ms"]["max"] == 500.0
        # `general` (#279) 은 표에 자리만 있다 — 러너는 폴백을 안 켜므로 골드에도 예측에도
        # 없어 precision/recall 이 빈 비율(1.0)로 찍힌다.
        assert set(ag["capabilities"]) == {"training", "life", "walk", "place", "general"}
        assert ag["capabilities"]["general"] == {"precision": 1.0, "recall": 1.0}
        assert r["divergence"]["favored"]["langgraph"] == (3 if r["run"] == 2 else 2)
        assert "clarify_07" in r["divergence"]["divergent_case_ids"]

    agg = summary["aggregate"]
    assert agg["stability"]["langgraph"]["unstable_case_ids"] == []
    assert agg["stability"]["agent"]["unstable_case_ids"] == ["mixed_01"]
    assert agg["divergence"]["in_every_run"] == ["clarify_07", "life_01"]
    assert agg["divergence"]["in_any_run"] == ["clarify_07", "life_01", "mixed_01"]
    assert agg["divergence"]["favored_sum_over_runs"] == {"langgraph": 7, "agent": 0, "neither": 0}
    assert agg["implementations"]["agent"]["silent_intent_loss"] == {"clarify_07": 3, "mixed_01": 1}
    assert agg["cost_difference_agent_vs_langgraph_pct"]["total_tokens"] == 100.0
    assert agg["implementations"]["agent"]["tokens"]["total"] == 3 * 80 * 220
    assert agg["implementations"]["langgraph"]["latency_ms_pooled"]["count"] == 240
    assert summary["recommendation"]["verdict"] == "retain_langgraph"
    assert summary["real_tester_holdout"]["status"] == "pending"


def test_a_perfect_tie_is_inconclusive_not_a_win() -> None:
    """둘이 똑같으면 '남긴다' 가 아니라 '결론 없음' 이다 — 기본값을 남기는 것은 D-055 ⑥ 의 몫."""
    loaded = []
    for meta, runs in _synthetic_runs():
        loaded.append((meta, {"langgraph": runs["langgraph"], "agent": list(runs["langgraph"])}))
    summary = v2.build_summary(CASES, loaded)
    assert summary["recommendation"]["verdict"] == "inconclusive"
    assert "tie" in summary["recommendation"]["reason"]


def test_summary_refuses_runs_from_different_source_commits() -> None:
    loaded = _synthetic_runs()
    loaded[1][0]["source_sha"] = "other"
    with pytest.raises(ValueError, match="different source commits"):
        v2.build_summary(CASES, loaded)


def test_run_file_round_trips_meta_and_cases(tmp_path: Path) -> None:
    meta, runs = _synthetic_runs()[1]
    path = tmp_path / "run_02.jsonl"
    v2.write_run(path, CASES, runs, meta=meta)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["kind"] == "meta"
    first_two = [json.loads(line)["implementation"] for line in lines[1:3]]
    assert tuple(first_two) == v2.execution_order(2, 0)  # 행 순서 = 실행 순서
    assert all(json.loads(line)["source_sha"] == "abc" for line in lines[1:])

    loaded_meta, loaded_runs = v2.load_run(path)
    assert loaded_meta["run"] == 2
    for name in v2.IMPLEMENTATIONS:
        assert [r.case_id for r in loaded_runs[name]] == [r.case_id for r in runs[name]]
        assert [r.attempt.plan for r in loaded_runs[name]] == [r.attempt.plan for r in runs[name]]
        assert [r.first_runner for r in loaded_runs[name]] == [r.first_runner for r in runs[name]]


def test_report_renders_every_required_section() -> None:
    report = v2._markdown_report(v2.build_summary(CASES, _synthetic_runs()))
    for heading in (
        "## 통제 설정",
        "## 실행별 지표",
        "## 3회 합산",
        "## 안정성",
        "## 두 구현이 갈린 곳",
        "## 조용한 의도 손실",
        "## 실패 계약",
        "## 실사용 테스터 홀드아웃",
        "## 지지되는 결론과 지지되지 않는 결론",
        "## 권고",
        "## 사람의 결정",
    ):
        assert heading in report, heading
    assert (
        "planner-first" in report and "LangChain 을 서로 배타적인 런타임 기술로 비교하는" in report
    )
    assert "`clarify_07`" in report and "retain_langgraph" in report
