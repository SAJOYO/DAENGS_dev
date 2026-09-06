"""골드 80개를 두 구현에 각각 먹이고 같은 자로 잰다 (#252, D-055 ⑥).

    uv run python -m tools.orchestrator_comparison.runner --limit 3   # 연기 시험
    uv run python -m tools.orchestrator_comparison.runner             # 전체

## 무엇을 재고 무엇을 안 재나

재는 것은 **능력 선택**이다 — 어느 능력을 부르고, 무엇을 핸드오프하고, 언제 되묻나.
도메인 답의 품질이 아니다. 그래서 어댑터를 가짜로 고정한다(카드 ⑦): 진짜를 쓰면
Training RAG 의 pgvector 조회와 Place 의 내부 HTTP 가 지연에 섞여, 재려는
"오케스트레이션 비용"이 도메인 비용에 묻힌다. 가짜는 즉시 OK 를 돌려주므로 **남는
시간이 곧 오케스트레이션 시간**이다.

## 왜 `build_orchestrator()` 를 안 부르나

카드가 그것을 지목한 이유는 **한 프로세스에 객체 둘**을 세우는 것이었고(#246 이 `kind`
인자를 만든 이유), 그 요구는 여기서 그대로 지킨다. 다만 `build_orchestrator` 는 어댑터를
받지 않아 가짜를 넣을 수 없다. 그래서 같은 구성을 직접 만든다 — 환경 변수를 토글해 가며
서버를 두 번 띄우는 일은 여전히 없다.

## RoutePlan 을 어떻게 꺼내나

**두 구현이 모두 `aggregate_results` 를 지난다.** LangGraph 는 미리 짠 계획을, 에이전트는
다 돌고 나서 되돌려 만든 계획을(D-055 ③) 그 함수에 넘긴다. 그래서 그 한 지점을 잡으면
**실제로 쓰인 계획**이 대칭적으로 잡힌다 — 응답에서 되짚어 재구성하면 payload 가 없어
`_semantic_plan_key` 가 비교하는 것과 어긋난다.

잡히지 않는 두 경우가 있고, 둘 다 의미 있는 결과다:
  · 라우터/모델 실패(O-14) — 계획 없이 FAILED. `schema_valid=False` 로 기록된다
  · 순수 스몰토크 — 계획이 애초에 없다. 같은 방식으로 기록된다

## 채점

`tools.router_benchmark.evaluate.evaluate_benchmark` 를 **그대로** 쓴다 (카드 ①).
지표를 새로 쓰면 v1~v8 여덟 세대와 비교가 끊긴다. 두 구현이 같은 함수를 지나야
숫자가 비교 가능해진다.

## 계약이 갈리는 곳 (카드 ⑤)

골드는 LangGraph 기준으로 동결돼 있다. 아래 둘에서 에이전트가 지는 것은 **성능이 아니라
계약 차이**이므로, 그대로 감점하면 비교가 거짓말이 된다. 여기서는 **감점하되 따로 센다** —
리포트가 "총점"과 "계약 차이를 뺀 점수"를 나란히 싣고, 판단은 사람이 한다.
  ⑴ CLARIFY — planner 는 실행 전에 선택 전체를 게이트하지만, 에이전트는 하나씩 부르며
     알게 되어 이미 나온 답이 있으면 되묻지 않는다.
  ⑵ 루프 중간 실패 — 에이전트는 부분 결과를 살린다.

## 명시 신호 케이스 (카드 ⑧)

`requested_capability` 가 오는 케이스는 두 구현이 **같은 코드**로 끝난다
(`resolve_deterministic_route`, D-036). 비교에 정보가 없으므로 리포트에서 따로 표시한다.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import statistics
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from daengs_backend.orchestration.contracts import (
    CapabilityName,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    PrincipalContext,
    RoutePlan,
)
from tools.router_benchmark.evaluate import evaluate_benchmark
from tools.router_benchmark.schemas import (
    AttemptValidation,
    GoldCase,
    PerformanceObservation,
    load_gold_cases,
)

RESULTS_DIR = Path(__file__).resolve().parents[2] / "evals" / "orchestration_router"
RESULTS_PATH = RESULTS_DIR / "comparison_v1_results.jsonl"
SUMMARY_PATH = RESULTS_DIR / "comparison_v1_summary.json"
REPORT_PATH = RESULTS_DIR / "comparison_v1_report.md"

#: 비교 대상. 키가 곧 리포트의 열 이름이다.
IMPLEMENTATIONS = ("langgraph", "agent")

#: 채점에 쓰는 `prompt_version`. 두 구현이 서로 다른 값을 내야 결과 행이 구분된다.
PROMPT_VERSION_BY_IMPL = {
    "langgraph": "semantic-router-ko-v7",
    "agent": "agent-ko-v1",
}

_PRINCIPAL = PrincipalContext(subject="comparison-runner", kind="ADMIN")


# ---------------------------------------------------------------------------
# 가짜 어댑터 — 재는 것은 능력 선택이지 도메인 답이 아니다 (카드 ⑦)
# ---------------------------------------------------------------------------


@dataclass
class FakeAdapter:
    """즉시 OK 를 돌려준다. 그래서 남는 시간이 곧 오케스트레이션 시간이다."""

    capability: CapabilityName

    async def run(self, request: CapabilityRequest, *, request_id: str) -> CapabilityResult:
        return CapabilityResult(
            capability=self.capability,
            status=CapabilityStatus.OK,
            data={"answer": f"(가짜 {self.capability.value} 어댑터)"},
            elapsed_ms=0,
        )


def _fake_adapters() -> dict[CapabilityName, FakeAdapter]:
    return {name: FakeAdapter(capability=name) for name in CapabilityName}


# ---------------------------------------------------------------------------
# 계량 — 비용·지연을 in-process 로 (카드 ②: 트레이싱 인프라가 필요 없다)
# ---------------------------------------------------------------------------


@dataclass
class Meter:
    """한 케이스의 모델 사용량. `turns` 가 이 카드의 핵심 숫자다.

    에이전트는 루프를 돌아서 토큰이 턴 수에 비례한다 — 두 구현의 비용 차이가 어디서
    오는지 이 숫자가 말한다. LangGraph 는 의미 라우터 호출 1회(스키마 실패 시 2회)다.
    """

    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def reset(self) -> None:
        self.turns = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def add(self, *, input_tokens: int | None, output_tokens: int | None) -> None:
        self.turns += 1
        self.input_tokens += int(input_tokens or 0)
        self.output_tokens += int(output_tokens or 0)

    def observation(self, latency_ms: float) -> PerformanceObservation:
        total = self.input_tokens + self.output_tokens
        return PerformanceObservation(
            latency_ms=latency_ms,
            input_tokens=self.input_tokens or None,
            output_tokens=self.output_tokens or None,
            total_tokens=total or None,
        )


def _metered_semantic_generate(meter: Meter):
    """LangGraph 의 의미 라우터 호출을 재는 transport.

    `semantic._generate_with_gemini` 를 그대로 두고 쓸 수 없다 — 그쪽은 응답 객체를
    버리고 parsed/text 만 돌려주어 `usage_metadata` 가 남지 않는다. 호출 자체는 같은
    설정으로 한다(모델·temperature·스키마) — 다르면 재는 대상이 달라진다.
    """
    from daengs_backend.orchestration.semantic import (
        ROUTER_MODEL_ID,
        SemanticRoutingDecision,
        _gemini_client,
    )

    async def generate(prompt: str) -> object:
        def _call() -> object:
            from google.genai import types

            response = _gemini_client().models.generate_content(
                model=ROUTER_MODEL_ID,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    candidate_count=1,
                    max_output_tokens=256,
                    response_mime_type="application/json",
                    response_json_schema=SemanticRoutingDecision.model_json_schema(),
                ),
            )
            usage = getattr(response, "usage_metadata", None)
            meter.add(
                input_tokens=getattr(usage, "prompt_token_count", None),
                output_tokens=getattr(usage, "candidates_token_count", None),
            )
            parsed = getattr(response, "parsed", None)
            return parsed if parsed is not None else getattr(response, "text", None)

        return await asyncio.to_thread(_call)

    return generate


def _agent_usage_callback(meter: Meter):
    """에이전트의 턴별 사용량을 세는 LangChain 콜백.

    모델 생성자에 넘긴다 — `with_config()` 로 감싸면 `RunnableBinding` 이 되어
    `create_agent` 가 기대하는 `bind_tools` 경로가 흔들린다.
    """
    from langchain_core.callbacks import BaseCallbackHandler

    class UsageMeter(BaseCallbackHandler):
        def on_llm_end(self, response: Any, **_: Any) -> None:
            for generations in getattr(response, "generations", []) or []:
                for generation in generations:
                    message = getattr(generation, "message", None)
                    usage = getattr(message, "usage_metadata", None) or {}
                    meter.add(
                        input_tokens=usage.get("input_tokens"),
                        output_tokens=usage.get("output_tokens"),
                    )

    return UsageMeter()


# ---------------------------------------------------------------------------
# 실제로 쓰인 RoutePlan 잡기
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _capture_route_plan(sink: dict[str, RoutePlan | None]) -> Iterator[None]:
    """두 구현이 함께 지나는 `aggregate_results` 에서 계획을 잡는다 (모듈 docstring).

    각 모듈이 이름으로 import 했으므로 **원본 모듈이 아니라 쓰는 쪽 네임스페이스**를
    갈아야 한다. 원본만 갈면 이미 바인딩된 참조가 그대로 남아 아무것도 안 잡힌다.
    """
    from daengs_backend.orchestration import graph as graph_mod
    from daengs_backend.orchestration.agent import service as agent_mod

    real = graph_mod.aggregate_results

    def spy(**kwargs: Any) -> Any:
        sink["plan"] = kwargs.get("route_plan")
        return real(**kwargs)

    graph_mod.aggregate_results = spy  # type: ignore[assignment]
    agent_mod.aggregate_results = spy  # type: ignore[assignment]
    try:
        yield
    finally:
        graph_mod.aggregate_results = real  # type: ignore[assignment]
        agent_mod.aggregate_results = real  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# 구현 둘 — 한 프로세스, 객체 둘
# ---------------------------------------------------------------------------


def build_pair(meters: dict[str, Meter]) -> dict[str, Any]:
    """가짜 어댑터를 문 두 구현을 만든다. 환경 변수를 토글하지 않는다."""
    from functools import partial

    from langchain_google_genai import ChatGoogleGenerativeAI

    from daengs_backend.config import settings
    from daengs_backend.orchestration.agent.service import (
        AGENT_MODEL_ID,
        AgentOrchestrationService,
    )
    from daengs_backend.orchestration.agent.tools import CapabilityToolbox
    from daengs_backend.orchestration.graph import OrchestrationEngine
    from daengs_backend.orchestration.semantic import GeminiSemanticRouter
    from daengs_backend.orchestration.service import AssistantOrchestrationService

    adapters = _fake_adapters()

    langgraph = AssistantOrchestrationService(
        engine=OrchestrationEngine(adapters),
        semantic_router=GeminiSemanticRouter(
            generate=_metered_semantic_generate(meters["langgraph"])
        ),
    )
    agent = AgentOrchestrationService(
        model=ChatGoogleGenerativeAI(
            model=AGENT_MODEL_ID,
            google_api_key=settings.gemini_api_key.get_secret_value(),
            timeout=settings.gemini_timeout_ms / 1_000,
            callbacks=[_agent_usage_callback(meters["agent"])],
        ),
        toolbox_factory=partial(CapabilityToolbox, adapters=adapters),
    )
    return {"langgraph": langgraph, "agent": agent}


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------


@dataclass
class CaseRun:
    case_id: str
    attempt: AttemptValidation
    performance: PerformanceObservation
    turns: int
    status: str
    error: str | None = None


async def run_case(orchestrator: Any, case: GoldCase, meter: Meter) -> CaseRun:
    """케이스 하나를 한 구현에 먹인다. 실패해도 다음 케이스로 넘어간다."""
    meter.reset()
    sink: dict[str, RoutePlan | None] = {"plan": None}
    started = time.perf_counter()
    status, error = "OK", None

    try:
        with _capture_route_plan(sink):
            response = await orchestrator.run(
                query=case.query,
                principal=_PRINCIPAL,
                context=dict(case.context or {}),
            )
        status = response.status.value
    except Exception as exc:  # noqa: BLE001 - 한 케이스의 실패가 실행 전체를 멈추지 않는다
        status, error = "RUNNER_ERROR", f"{type(exc).__name__}: {exc}"

    latency_ms = (time.perf_counter() - started) * 1_000
    plan = sink["plan"]
    return CaseRun(
        case_id=case.case_id,
        attempt=AttemptValidation(schema_valid=plan is not None, plan=plan),
        performance=meter.observation(latency_ms),
        turns=meter.turns,
        status=status,
        error=error,
    )


async def run_all(cases: list[GoldCase]) -> dict[str, list[CaseRun]]:
    meters = {name: Meter() for name in IMPLEMENTATIONS}
    pair = build_pair(meters)
    runs: dict[str, list[CaseRun]] = {name: [] for name in IMPLEMENTATIONS}

    for index, case in enumerate(cases, start=1):
        for name in IMPLEMENTATIONS:
            run = await run_case(pair[name], case, meters[name])
            runs[name].append(run)
            marker = "ok" if run.error is None else "ERR"
            print(
                f"  [{index:>3}/{len(cases)}] {case.case_id:<24} {name:<10} "
                f"{marker} status={run.status:<9} turns={run.turns} "
                f"{run.performance.latency_ms:.0f}ms"
                + (f"  {run.error}" if run.error else "")
            )
    return runs


# ---------------------------------------------------------------------------
# 산출물
# ---------------------------------------------------------------------------


def _head_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:  # noqa: BLE001 - SHA 가 없다고 벤치마크를 멈추지 않는다
        return "unknown"


def _explicit_signal_case_ids(cases: list[GoldCase]) -> set[str]:
    """명시 신호 케이스 (카드 ⑧) — 두 구현이 같은 코드로 끝나 비교에 정보가 없다."""
    return {
        case.case_id
        for case in cases
        if isinstance(case.context, dict) and case.context.get("requested_capability")
    }


def _divergence(
    cases: list[GoldCase], runs: dict[str, list[CaseRun]]
) -> dict[str, Any]:
    """두 구현이 **의미상** 갈린 케이스만 추린다.

    `RoutePlan` 을 그대로 비교하면 안 된다 — `model`·`prompt_version` 이 구현마다
    구조적으로 달라서 80개가 전부 "다름"으로 잡힌다. 채점기의 정규화 키를 쓴다.

    카드 ⑤ 가 요구한 "감점하되 따로 센다"를 여기서 기계적으로 판정한다. 계약 차이의
    표식은 **골드가 CLARIFY 인데 구현이 실행해 버린 것** 이다: planner 는 실행 전에
    게이트하지만 에이전트는 하나씩 부르며 알게 되어, 이미 나온 답이 있으면 되묻지 않는다.
    사람이 눈으로 고르지 않고 이 규칙으로 세는 이유는, 다시 돌렸을 때 같은 답이
    나와야 하기 때문이다.
    """
    from tools.router_benchmark.evaluate import _semantic_plan_key

    gold_by_id = {case.case_id: case for case in cases}
    plans = {
        name: {run.case_id: run.attempt.plan for run in runs[name]}
        for name in IMPLEMENTATIONS
    }

    rows: list[dict[str, Any]] = []
    contract_ids: list[str] = []
    for case in cases:
        keys = {n: _semantic_plan_key(plans[n][case.case_id]) for n in IMPLEMENTATIONS}
        if keys[IMPLEMENTATIONS[0]] == keys[IMPLEMENTATIONS[1]]:
            continue
        gold_key = _semantic_plan_key(gold_by_id[case.case_id].gold_route_plan)
        matches = {n: keys[n] == gold_key for n in IMPLEMENTATIONS}
        gold_clarifies = gold_by_id[case.case_id].gold_route_plan.clarify is not None
        losers = [n for n in IMPLEMENTATIONS if not matches[n]]
        contract = gold_clarifies and any(
            (plans[n][case.case_id] is not None and plans[n][case.case_id].requests)
            for n in losers
        )
        if contract:
            contract_ids.append(case.case_id)
        rows.append(
            {
                "case_id": case.case_id,
                "category": case.category,
                "matches_gold": matches,
                "contract_difference": contract,
            }
        )

    adjusted: dict[str, float] = {}
    for name in IMPLEMENTATIONS:
        scored = [c for c in cases if c.case_id not in contract_ids]
        hits = sum(
            _semantic_plan_key(plans[name][c.case_id])
            == _semantic_plan_key(gold_by_id[c.case_id].gold_route_plan)
            for c in scored
        )
        adjusted[name] = round(hits / len(scored), 4) if scored else 0.0

    return {
        "divergent_case_count": len(rows),
        "cases": rows,
        "contract_difference_case_ids": contract_ids,
        "exact_match_excluding_contract_differences": adjusted,
    }


def build_summary(
    cases: list[GoldCase], runs: dict[str, list[CaseRun]]
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "benchmark_id": "orchestrator-comparison-v1",
        "card": "#252",
        "decision": "D-055",
        # 카드 메모 ③ — 랭그래프 고도화가 병렬로 돌면 기준선이 움직인다.
        "head_commit": _head_sha(),
        "gold_file": "gold_v1.jsonl",
        "scored_case_count": len(cases),
        "explicit_signal_case_ids": sorted(_explicit_signal_case_ids(cases)),
        "divergence": _divergence(cases, runs),
        "implementations": {},
    }

    for name in IMPLEMENTATIONS:
        case_runs = runs[name]
        evaluation = evaluate_benchmark(
            cases,
            {run.case_id: [run.attempt] for run in case_runs},
            {run.case_id: run.performance for run in case_runs},
            prompt_version=PROMPT_VERSION_BY_IMPL[name],  # type: ignore[arg-type]
            model_id="gemini-3.1-flash-lite",
        )
        turns = [run.turns for run in case_runs]
        latencies = [
            run.performance.latency_ms
            for run in case_runs
            if run.performance.latency_ms is not None
        ]
        summary["implementations"][name] = {
            "prompt_version": PROMPT_VERSION_BY_IMPL[name],
            "summary": evaluation.summary.model_dump(mode="json"),
            "llm_turns": {
                "total": sum(turns),
                "mean": round(statistics.fmean(turns), 2) if turns else 0,
                "max": max(turns) if turns else 0,
            },
            "latency_ms": {
                "mean": round(statistics.fmean(latencies), 1) if latencies else None,
                "max": round(max(latencies), 1) if latencies else None,
            },
            "runner_errors": [
                {"case_id": run.case_id, "error": run.error}
                for run in case_runs
                if run.error
            ],
        }
    return summary


def write_artifacts(
    cases: list[GoldCase], runs: dict[str, list[CaseRun]], summary: dict[str, Any]
) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    with RESULTS_PATH.open("w", encoding="utf-8") as handle:
        for name in IMPLEMENTATIONS:
            for run in runs[name]:
                handle.write(
                    json.dumps(
                        {
                            "implementation": name,
                            "case_id": run.case_id,
                            "status": run.status,
                            "turns": run.turns,
                            "latency_ms": run.performance.latency_ms,
                            "input_tokens": run.performance.input_tokens,
                            "output_tokens": run.performance.output_tokens,
                            "plan": run.attempt.plan.model_dump(mode="json")
                            if run.attempt.plan
                            else None,
                            "error": run.error,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

    SUMMARY_PATH.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    REPORT_PATH.write_text(_markdown_report(summary), encoding="utf-8")


def _markdown_report(summary: dict[str, Any]) -> str:
    impls = summary["implementations"]
    rows = []
    for label, key in (
        ("RoutePlan 완전 일치", "exact_route_plan_match"),
        ("실행 precision", "executable_precision"),
        ("실행 recall", "executable_recall"),
        ("핸드오프 precision", "handoff_precision"),
        ("핸드오프 recall", "handoff_recall"),
        ("CLARIFY recall  ⑤", "clarify_recall"),
        ("헛 CLARIFY 비율  ⑤", "false_positive_clarify_rate"),
        ("스키마 유효율", "final_schema_valid_rate"),
        ("금지 실행 건수", "forbidden_execute_count"),
    ):
        values = [impls[name]["summary"].get(key) for name in IMPLEMENTATIONS]
        rendered = [f"{v:.3f}" if isinstance(v, (int, float)) else "–" for v in values]
        rows.append(f"| {label} | {rendered[0]} | {rendered[1]} |")

    turn_row = " | ".join(
        f"{impls[name]['llm_turns']['total']} (평균 {impls[name]['llm_turns']['mean']})"
        for name in IMPLEMENTATIONS
    )
    latency_row = " | ".join(
        f"{impls[name]['latency_ms']['mean']}" for name in IMPLEMENTATIONS
    )

    div = summary["divergence"]
    div_rows = [
        "| 케이스 | 분류 | langgraph | agent | 계약 차이 ⑤ |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in div["cases"]:
        mark = lambda ok: "O" if ok else "**X**"
        div_rows.append(
            f"| `{row['case_id']}` | {row['category']} | "
            f"{mark(row['matches_gold']['langgraph'])} | {mark(row['matches_gold']['agent'])} | "
            f"{'예' if row['contract_difference'] else '아니오'} |"
        )
    divergence_table = chr(10).join(div_rows) if div["cases"] else "_갈린 케이스 없음._"
    adjusted = div["exact_match_excluding_contract_differences"]

    return f"""# 오케스트레이터 비교 v1 (#252 · D-055 ⑥)

- 골드: `{summary["gold_file"]}` · 채점 케이스 {summary["scored_case_count"]}개
- 커밋: `{summary["head_commit"]}`
- 채점기: `tools/router_benchmark/evaluate.py` (v1~v8 과 **같은 자**)
- 어댑터: 가짜 — 재는 것은 능력 선택이지 도메인 답이 아니다

## 지표

| 지표 | langgraph | agent |
| --- | --- | --- |
{chr(10).join(rows)}
| LLM 턴 수 | {turn_row} |
| 평균 지연(ms) | {latency_row} |

## 두 구현이 갈린 곳

의미상 갈린 케이스 **{div["divergent_case_count"]}개** / {summary["scored_case_count"]}개. (`RoutePlan` 을 그대로 비교하면
`model`·`prompt_version` 때문에 전부 다르게 잡히므로, 채점기의 정규화 키로 본다.)

{divergence_table}

계약 차이를 뺀 완전 일치: **langgraph {adjusted["langgraph"]:.3f} · agent {adjusted["agent"]:.3f}**

## 읽는 법

**턴 수가 비용의 원인이다.** 에이전트는 루프를 돌아 토큰이 턴 수에 비례한다. 두 구현의
비용 차이가 지표 차이보다 크다면, 그건 품질이 아니라 구조에서 오는 것이다.

**계약이 갈리는 두 곳은 감점을 그대로 믿지 말 것** (카드 ⑤). 골드는 LangGraph 기준으로
동결돼 있다:
1. **CLARIFY** — 에이전트는 이미 나온 답이 있으면 되묻지 않는다
2. **루프 중간 실패** — 에이전트는 부분 결과를 살린다

이 둘에서 에이전트가 지는 것은 성능이 아니라 계약 차이다.

**명시 신호 케이스**({len(summary["explicit_signal_case_ids"])}개)는 두 구현이 같은 코드로
끝난다 (D-036). 비교에 정보가 없다: {", ".join(summary["explicit_signal_case_ids"]) or "없음"}

## 이 리포트가 말하는 것

**v1 은 계약이 갈린 상태의 측정이다.** 골드는 LangGraph 의 계약으로 동결돼 있고,
에이전트는 다른 계약으로 돈다. 그래서 총점을 그대로 "품질 차이"로 읽으면 안 된다.

- 의미상 갈린 케이스 **{div["divergent_case_count"]}건** / {summary["scored_case_count"]}건 —
  나머지는 두 구현이 똑같이 골랐다
- 그중 **{len(div["contract_difference_case_ids"])}건이 계약 차이**다 (카드 ⑤⑴)
- 계약 차이를 빼면 완전 일치는 **{adjusted["langgraph"]:.3f} 대 {adjusted["agent"]:.3f}** 로 좁혀진다
- **남는 차이는 비용이다** — 턴 {impls["langgraph"]["llm_turns"]["total"]} 대 {impls["agent"]["llm_turns"]["total"]},
  평균 지연 {impls["langgraph"]["latency_ms"]["mean"]}ms 대 {impls["agent"]["latency_ms"]["mean"]}ms.
  이건 계약 차이로 설명되지 않고 구조에서 온다

**전제를 맞춘 재측정이 후속 카드다** (메모 ⑥). 골드를 고치는 것이 아니라 에이전트가
같은 계약으로 돌게 한 뒤 같은 러너로 다시 재고, 그것이 `comparison_v2` 가 된다.
이 리포트는 그때도 그대로 남아 **왜 프롬프트를 바꿨는지**의 근거가 된다.

## 사람의 결정

<!-- 어느 구현을 남길지. v2 결과까지 보고 D-055 ⑥ 에 반영한 뒤, 진 쪽을 지우는 카드를 연다. -->
"""


# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="두 오케스트레이터 비교 (#252)")
    parser.add_argument("--limit", type=int, default=None, help="앞에서 N개만 (연기 시험)")
    args = parser.parse_args()

    cases = list(load_gold_cases())
    if args.limit:
        cases = cases[: args.limit]
    print(f"골드 {len(cases)}개 × 구현 {len(IMPLEMENTATIONS)}개")

    runs = asyncio.run(run_all(cases))
    summary = build_summary(cases, runs)
    write_artifacts(cases, runs, summary)

    print(f"\n결과  {RESULTS_PATH}")
    print(f"요약  {SUMMARY_PATH}")
    print(f"리포트 {REPORT_PATH}")
    for name in IMPLEMENTATIONS:
        impl = summary["implementations"][name]
        print(
            f"  {name:<10} exact={impl['summary'].get('exact_route_plan_match')} "
            f"turns={impl['llm_turns']['total']} "
            f"latency={impl['latency_ms']['mean']}ms "
            f"errors={len(impl['runner_errors'])}"
        )


if __name__ == "__main__":
    main()
