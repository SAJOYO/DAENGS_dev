"""비교 v2 — 같은 계약 아래에서 두 오케스트레이션 패턴을 세 번 잰다 (#272, D-055 ⑥).

    uv run python -m daengs_evals.orchestrator_comparison.runner_v2 --run 1 --limit 3   # 연기 시험
    uv run python -m daengs_evals.orchestrator_comparison.runner_v2 --run 1             # 반복 1
    uv run python -m daengs_evals.orchestrator_comparison.runner_v2 --run 2
    uv run python -m daengs_evals.orchestrator_comparison.runner_v2 --run 3
    uv run python -m daengs_evals.orchestrator_comparison.runner_v2 --summarize         # 요약·리포트
    uv run python -m daengs_evals.orchestrator_comparison.runner_v2 --all               # 위 넷을 한 번에

## v1(`runner.py`) 과 무엇이 다른가

v1 은 계약이 갈린 상태의 측정이었다 (#252 리포트). v2 는 에이전트를 동결된 CLARIFY 계약에
맞춘 뒤(`agent/`, #272) 같은 골드 80개를 다시 잰다. 러너 자체도 셋을 고친다.

1. **실행 순서 편향.** v1 은 케이스마다 LangGraph 를 먼저 돌렸다. 여기서는 케이스 인덱스의
   짝홀로 선행 구현을 번갈고, 반복마다 시작 구현을 뒤집는다 — 각 구현이 절반씩 먼저 돈다.
   실제 순서를 케이스마다 기록한다. 실행은 여전히 **순차**다 (동시 실행은 프로바이더 쪽
   지연이 섞인다).
2. **예열.** 반복을 시작하기 전에 구현마다 비채점 호출을 한 번 한다. 첫 호출의 연결·캐시
   비용이 한쪽 케이스에만 붙지 않게 하려는 것이고, 지표에는 넣지 않는다.
3. **세 번 반복, 각각 보존.** `comparison_v2_run_0N_results.jsonl` 세 개가 원본이고
   `--summarize` 는 그 셋만 읽어 `comparison_v2_summary.json` · `comparison_v2_report.md`
   를 만든다. 반복은 같은 케이스의 종속 관측이라 **합쳐서 유의성을 주장하지 않는다** —
   실행별로 따로 보이고, 3회 일치도로 안정성을 말한다.

## 무엇을 재고 무엇을 안 재나 (v1 과 같다)

능력 선택 · 계약 준수 · 오케스트레이션 오버헤드. 어댑터는 가짜(즉시 OK)라 Training RAG ·
Life 답 품질 · Place HTTP · DB 지연 · 운영 end-to-end 지연은 재지 않는다. 실패 계약
(오류·타임아웃·혼합)은 `tests/test_orchestrator_failure_contract.py` 가 결정론으로 따로
검증하고, 이 80케이스 점수와 섞지 않는다.

## RoutePlan 을 어떻게 꺼내나

#272 부터 **두 구현이 모두 같은 `OrchestrationEngine` 에 RoutePlan 을 넘긴다** — LangGraph
는 의미 라우터의 결정을, 에이전트는 툴 루프의 결정을 같은 `assemble_route_plan` 으로 조립해
넘긴다. 그래서 엔진을 감싼 `RecordingEngine` 하나로 실제로 쓰인 계획이 대칭적으로 잡힌다.
v1 처럼 모듈 전역을 갈아끼우지 않는다.

잡히지 않는 두 경우는 v1 과 같다 — 라우터/모델 실패(O-14) 와 순수 스몰토크. 둘 다 계획이
없어 `schema_valid=False` 로 기록된다.

## 재현 규칙

세 반복은 **같은 소스 커밋**에서 돌아야 한다. 각 결과 파일의 meta 행에 `source_sha` 와
작업트리 dirty 여부를 적고, `--summarize` 는 셋의 `source_sha` 와 설정이 같지 않으면 멈춘다.
반복 사이에 코드·프롬프트·설정을 바꾸지 않는다.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import importlib.metadata
import json
import statistics
import subprocess
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from daengs_backend.orchestration.contracts import (
    CapabilityName,
    PrincipalContext,
    RoutePlan,
)
from daengs_backend.orchestration.graph import CapabilityAdapter, OrchestrationEngine
from daengs_evals import BACKEND_DIR
from daengs_evals.orchestrator_comparison.runner import (
    Meter,
    _agent_usage_callback,
    _explicit_signal_case_ids,
    _fake_adapters,
)
from daengs_evals.router_benchmark.evaluate import (
    _execute_counter,
    _percentile,
    _ratio,
    _semantic_plan_key,
    evaluate_benchmark,
)
from daengs_evals.router_benchmark.schemas import (
    AttemptValidation,
    GoldCase,
    PerformanceObservation,
    load_gold_cases,
)

RESULTS_DIR = BACKEND_DIR / "evals" / "orchestration_router"
SUMMARY_PATH = RESULTS_DIR / "comparison_v2_summary.json"
REPORT_PATH = RESULTS_DIR / "comparison_v2_report.md"

BENCHMARK_ID = "orchestrator-comparison-v2"
CARD = "#272"
DECISION = "D-055"
GOLD_FILE = "gold_v1.jsonl"

#: 비교 대상. 키가 곧 리포트의 열 이름이다. 순서는 `first_runner` 의 기준이 된다.
IMPLEMENTATIONS = ("langgraph", "agent")
RUN_NUMBERS = (1, 2, 3)
#: 예열에 쓰는 골드 케이스 (비채점). 어느 케이스든 "같은 것 하나" 면 된다.
WARMUP_CASE_INDEX = 0
#: 이 두 경로에 관계된 패키지. 리포트가 버전을 박는다.
RELEVANT_PACKAGES = (
    "langchain",
    "langchain-core",
    "langchain-google-genai",
    "langgraph",
    "google-genai",
    "pydantic",
)
_LANGGRAPH_IMPL_PATHS = tuple(
    BACKEND_DIR / "src/daengs_backend/orchestration" / name
    for name in ("graph.py", "planner.py", "semantic.py", "service.py", "aggregate.py")
)
_AGENT_IMPL_PATHS = (BACKEND_DIR / "src/daengs_backend/orchestration/agent",)

_PRINCIPAL = PrincipalContext(subject="comparison-runner", kind="ADMIN")


def results_path(run: int) -> Path:
    return RESULTS_DIR / f"comparison_v2_run_{run:02d}_results.jsonl"


# ---------------------------------------------------------------------------
# 실행 순서 균형
# ---------------------------------------------------------------------------


def first_runner(run: int, index: int) -> str:
    """케이스 `index`(0부터) 에서 먼저 도는 구현.

    반복 1 의 인덱스 0 은 `IMPLEMENTATIONS[0]`(langgraph) 이 먼저다. 인덱스마다 번갈고,
    반복이 바뀌면 시작 구현이 뒤집힌다 — 그래서 같은 케이스가 반복 1·3 에서는 같은 순서,
    반복 2 에서는 반대 순서로 돈다.
    """
    return IMPLEMENTATIONS[(index + run - 1) % 2]


def execution_order(run: int, index: int) -> tuple[str, str]:
    first = first_runner(run, index)
    second = IMPLEMENTATIONS[1] if first == IMPLEMENTATIONS[0] else IMPLEMENTATIONS[0]
    return first, second


# ---------------------------------------------------------------------------
# 구현 둘 — 한 프로세스, 객체 둘. 실제로 쓰인 RoutePlan 은 엔진에서 잡는다
# ---------------------------------------------------------------------------


class RecordingEngine(OrchestrationEngine):
    """엔진에 닿은 RoutePlan 을 `sink["plan"]` 에 남긴다 (모듈 docstring)."""

    def __init__(
        self, adapters: Mapping[CapabilityName, CapabilityAdapter], sink: dict[str, Any]
    ) -> None:
        super().__init__(adapters)
        self._sink = sink

    async def run(self, *, route_plan: RoutePlan, **kwargs: Any) -> Any:  # type: ignore[override]
        self._sink["plan"] = route_plan
        return await super().run(route_plan=route_plan, **kwargs)


def _metered_semantic_generate(meter: Meter, client: Any = None) -> Callable[[str], Any]:
    """LangGraph 의 의미 라우터 호출을 재는 transport.

    `semantic._generate_with_gemini` 는 응답 객체를 버려 `usage_metadata` 가 남지 않으므로
    여기서 같은 호출을 하되 사용량을 센다. 생성 설정은 `router_generation_config()` **그
    객체**다 — v1 은 숫자를 다시 적었고, 그러면 운영과 벤치마크가 조용히 갈릴 수 있다.
    """
    from daengs_backend.orchestration.semantic import (
        ROUTER_MODEL_ID,
        _gemini_client,
        router_generation_config,
    )

    async def generate(prompt: str) -> object:
        def _call() -> object:
            response = (client or _gemini_client()).models.generate_content(
                model=ROUTER_MODEL_ID,
                contents=prompt,
                config=router_generation_config(),
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


def build_pair(meters: dict[str, Meter], sink: dict[str, Any]) -> dict[str, Any]:
    """가짜 어댑터를 문 두 구현. 환경 변수를 토글하지 않는다.

    에이전트의 모델은 런타임과 **같은 `build_agent_model`** 로 만든다 — 러너가 자기 모델을
    따로 조립하면 "운영 후보를 쟀다" 가 거짓이 된다. v1 은 그 자리에서 temperature 를 빠뜨렸다.
    """
    from daengs_backend.orchestration.agent.service import (
        AgentOrchestrationService,
        build_agent_model,
    )
    from daengs_backend.orchestration.semantic import GeminiSemanticRouter
    from daengs_backend.orchestration.service import AssistantOrchestrationService

    adapters = _fake_adapters()
    langgraph = AssistantOrchestrationService(
        engine=RecordingEngine(adapters, sink),
        semantic_router=GeminiSemanticRouter(
            generate=_metered_semantic_generate(meters["langgraph"])
        ),
    )
    agent = AgentOrchestrationService(
        model=build_agent_model(callbacks=[_agent_usage_callback(meters["agent"])]),
        engine=RecordingEngine(adapters, sink),
    )
    return {"langgraph": langgraph, "agent": agent}


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------


@dataclass
class CaseRun:
    case_id: str
    implementation: str
    run: int
    first_runner: str
    position: int
    attempt: AttemptValidation
    performance: PerformanceObservation
    turns: int
    status: str
    error: str | None = None


async def run_case(
    orchestrator: Any,
    case: GoldCase,
    meter: Meter,
    sink: dict[str, Any],
    *,
    implementation: str,
    run: int,
    first: str,
    position: int,
) -> CaseRun:
    """케이스 하나를 한 구현에 먹인다. 실패해도 다음 케이스로 넘어간다."""
    meter.reset()
    sink["plan"] = None
    started = time.perf_counter()
    status, error = "OK", None
    try:
        response = await orchestrator.run(
            query=case.query, principal=_PRINCIPAL, context=dict(case.context or {})
        )
        status = response.status.value
    except Exception as exc:  # noqa: BLE001 - 한 케이스의 실패가 실행 전체를 멈추지 않는다
        status, error = "RUNNER_ERROR", f"{type(exc).__name__}: {exc}"
    latency_ms = (time.perf_counter() - started) * 1_000
    plan = sink["plan"]
    return CaseRun(
        case_id=case.case_id,
        implementation=implementation,
        run=run,
        first_runner=first,
        position=position,
        attempt=AttemptValidation(schema_valid=plan is not None, plan=plan),
        performance=meter.observation(latency_ms),
        turns=meter.turns,
        status=status,
        error=error,
    )


async def warm_up(
    pair: Mapping[str, Any],
    meters: Mapping[str, Meter],
    sink: dict[str, Any],
    case: GoldCase,
    *,
    run: int,
) -> dict[str, Any]:
    """구현마다 비채점 호출 한 번. 순서는 그 반복의 첫 케이스 순서를 따른다."""
    record: dict[str, Any] = {"case_id": case.case_id, "order": [], "results": {}}
    for position, name in enumerate(execution_order(run, 0), start=1):
        result = await run_case(
            pair[name],
            case,
            meters[name],
            sink,
            implementation=name,
            run=run,
            first=name,
            position=position,
        )
        record["order"].append(name)
        record["results"][name] = {
            "status": result.status,
            "turns": result.turns,
            "latency_ms": round(result.performance.latency_ms or 0.0, 1),
            "error": result.error,
        }
    return record


async def run_repetition(
    cases: Sequence[GoldCase],
    run: int,
    pair: Mapping[str, Any],
    meters: Mapping[str, Meter],
    sink: dict[str, Any],
    *,
    log: Callable[[str], None] = print,
) -> dict[str, list[CaseRun]]:
    """반복 하나. 케이스마다 두 구현을 `execution_order` 대로 **순차** 실행한다."""
    runs: dict[str, list[CaseRun]] = {name: [] for name in IMPLEMENTATIONS}
    for index, case in enumerate(cases):
        first, _ = execution_order(run, index)
        for position, name in enumerate(execution_order(run, index), start=1):
            result = await run_case(
                pair[name],
                case,
                meters[name],
                sink,
                implementation=name,
                run=run,
                first=first,
                position=position,
            )
            runs[name].append(result)
            marker = "ok" if result.error is None else "ERR"
            log(
                f"  r{run} [{index + 1:>3}/{len(cases)}] {case.case_id:<24} {name:<10} "
                f"#{position} {marker} status={result.status:<9} turns={result.turns} "
                f"{result.performance.latency_ms:.0f}ms"
                + (f"  {result.error}" if result.error else "")
            )
    return runs


# ---------------------------------------------------------------------------
# 출처 · 설정 — 결과 파일이 "무엇을 쟀나" 를 스스로 말하게
# ---------------------------------------------------------------------------


def _git(*args: str, strip: bool = True) -> str:
    try:
        output = subprocess.run(
            ["git", *args], capture_output=True, text=True, check=True, cwd=BACKEND_DIR
        ).stdout
    except Exception:  # noqa: BLE001 - git 이 없다고 벤치마크를 멈추지 않는다
        return "unknown"
    return output.strip() if strip else output


def _dirty_tracked_files() -> list[str]:
    """추적 파일의 변경만. 이 러너가 새로 만드는 결과 파일(untracked)은 dirty 가 아니다.

    porcelain 행은 `XY 경로` 라 앞 공백이 뜻을 가진다 — 통째로 strip 하면 첫 행의 경로가
    한 글자 잘린다. 그래서 strip 없이 받아 행마다 자른다.
    """
    status = _git("status", "--porcelain", "--untracked-files=no", strip=False)
    if status == "unknown":
        return ["unknown"]
    return [line[3:] for line in status.splitlines() if line.strip()]


def source_provenance() -> dict[str, Any]:
    dirty = _dirty_tracked_files()
    return {
        "benchmark_source_sha": _git("rev-parse", "HEAD"),
        "source_dirty_files": dirty,
        "dev_source_sha": _git("merge-base", "HEAD", "origin/dev"),
        "agent_implementation_sha": _git(
            "log", "-1", "--format=%H", "--", *map(str, _AGENT_IMPL_PATHS)
        ),
        "langgraph_implementation_sha": _git(
            "log", "-1", "--format=%H", "--", *map(str, _LANGGRAPH_IMPL_PATHS)
        ),
        "packages": {name: _package_version(name) for name in RELEVANT_PACKAGES},
    }


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not installed"


def controlled_settings() -> dict[str, Any]:
    """두 구현의 통제 설정. **코드의 상수에서 읽는다** — 과거 JSON 을 믿지 않는다."""
    from daengs_backend.config import settings
    from daengs_backend.orchestration.agent.service import (
        AGENT_MAX_RETRIES,
        AGENT_MODEL_ID,
        AGENT_PROMPT_VERSION,
    )
    from daengs_backend.orchestration.semantic import (
        PROMPT_VERSION,
        ROUTER_CANDIDATE_COUNT,
        ROUTER_MAX_OUTPUT_TOKENS,
        ROUTER_MODEL_ID,
        ROUTER_TEMPERATURE,
    )

    return {
        "model_id": {"langgraph": ROUTER_MODEL_ID, "agent": AGENT_MODEL_ID},
        "temperature": {"langgraph": ROUTER_TEMPERATURE, "agent": ROUTER_TEMPERATURE},
        "candidate_count": {"langgraph": ROUTER_CANDIDATE_COUNT, "agent": ROUTER_CANDIDATE_COUNT},
        "max_output_tokens_per_call": {
            "langgraph": ROUTER_MAX_OUTPUT_TOKENS,
            "agent": ROUTER_MAX_OUTPUT_TOKENS,
        },
        "provider_timeout_ms": {
            "langgraph": settings.gemini_timeout_ms,
            "agent": settings.gemini_timeout_ms,
        },
        "provider_retries": {
            "langgraph": "none (google-genai retry_options unset → stop_after_attempt(1))",
            "agent": f"none (max_retries={AGENT_MAX_RETRIES} → stop_after_attempt(1))",
        },
        "schema_retry": {
            "langgraph": "once on schema failure (O-14)",
            "agent": "none — tool-call validity is enforced by the API",
        },
        "loop_bounds": {
            "langgraph": "single call",
            "agent": (
                f"recursion_limit={settings.agent_recursion_limit}, "
                f"turn_timeout_ms={settings.agent_turn_timeout_ms}"
            ),
        },
        "selection_surface": {
            "langgraph": "structured output (response_json_schema=SemanticRoutingDecision)",
            "agent": "function calling over 7 argument-less tools (reply_socially: intent)",
        },
        "model_visible_input": {
            "langgraph": "policy prompt + ROUTING_METADATA + USER_QUERY",
            "agent": "system prompt + ROUTING_METADATA + USER_QUERY (same keys, same validation)",
        },
        "prompt_version": {"langgraph": PROMPT_VERSION, "agent": AGENT_PROMPT_VERSION},
        "principal": f"{_PRINCIPAL.kind} {_PRINCIPAL.subject}",
        "locale": "ko-KR",
        "requested_capability": None,
        "adapters": "FakeAdapter — immediate OK for every capability (same as v1)",
        "credential": "settings.gemini_api_key — never printed",
        "execution": "sequential; first runner alternates by case index and inverts per run",
        "warmup": "one non-scored invocation per implementation per run, excluded from metrics",
    }


# ---------------------------------------------------------------------------
# 저장 · 적재
# ---------------------------------------------------------------------------


def case_record(result: CaseRun, source_sha: str) -> dict[str, Any]:
    return {
        "kind": "case",
        "run": result.run,
        "implementation": result.implementation,
        "case_id": result.case_id,
        "first_runner": result.first_runner,
        "position": result.position,
        "status": result.status,
        "turns": result.turns,
        "latency_ms": result.performance.latency_ms,
        "input_tokens": result.performance.input_tokens,
        "output_tokens": result.performance.output_tokens,
        "plan": result.attempt.plan.model_dump(mode="json") if result.attempt.plan else None,
        "error": result.error,
        "source_sha": source_sha,
    }


def write_run(
    path: Path,
    cases: Sequence[GoldCase],
    runs: Mapping[str, Sequence[CaseRun]],
    *,
    meta: Mapping[str, Any],
) -> None:
    """meta 행 하나 뒤에 케이스 행들. 행 순서는 실제 실행 순서다."""
    by_key = {
        (result.case_id, result.implementation): result
        for name in IMPLEMENTATIONS
        for result in runs[name]
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({"kind": "meta", **meta}, ensure_ascii=False) + "\n")
        for index, case in enumerate(cases):
            for name in execution_order(int(meta["run"]), index):
                record = case_record(by_key[(case.case_id, name)], str(meta["source_sha"]))
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_run(path: Path) -> tuple[dict[str, Any], dict[str, list[CaseRun]]]:
    meta: dict[str, Any] | None = None
    runs: dict[str, list[CaseRun]] = {name: [] for name in IMPLEMENTATIONS}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("kind") == "meta":
            meta = row
            continue
        plan = RoutePlan.model_validate(row["plan"]) if row.get("plan") else None
        runs[row["implementation"]].append(
            CaseRun(
                case_id=row["case_id"],
                implementation=row["implementation"],
                run=int(row["run"]),
                first_runner=row["first_runner"],
                position=int(row["position"]),
                attempt=AttemptValidation(schema_valid=plan is not None, plan=plan),
                performance=PerformanceObservation(
                    latency_ms=row.get("latency_ms"),
                    input_tokens=row.get("input_tokens"),
                    output_tokens=row.get("output_tokens"),
                    total_tokens=((row.get("input_tokens") or 0) + (row.get("output_tokens") or 0))
                    or None,
                ),
                turns=int(row["turns"]),
                status=row["status"],
                error=row.get("error"),
            )
        )
    if meta is None:
        raise ValueError(f"{path} has no meta row")
    return meta, runs


# ---------------------------------------------------------------------------
# 지표
# ---------------------------------------------------------------------------


def _plans_by_case(runs: Sequence[CaseRun]) -> dict[str, RoutePlan | None]:
    return {result.case_id: result.attempt.plan for result in runs}


def capability_metrics(
    cases: Sequence[GoldCase], plans: Mapping[str, RoutePlan | None]
) -> dict[str, dict[str, float]]:
    """능력별 precision / recall. 채점기는 place 를 따로 안 내서 여기서 센다."""
    gold_total: Counter[str] = Counter()
    pred_total: Counter[str] = Counter()
    true_total: Counter[str] = Counter()
    for case in cases:
        gold = _execute_counter(case.gold_route_plan)
        pred = _execute_counter(plans.get(case.case_id))
        gold_total += gold
        pred_total += pred
        true_total += gold & pred
    return {
        name.value: {
            "precision": _ratio(true_total[name.value], pred_total[name.value], empty=1.0),
            "recall": _ratio(true_total[name.value], gold_total[name.value], empty=1.0),
        }
        for name in CapabilityName
    }


def _intents(plan: RoutePlan | None) -> set[str]:
    if plan is None:
        return set()
    return {request.capability.value for request in plan.requests} | {
        f"handoff:{handoff.target}" for handoff in plan.handoffs
    }


def silent_intent_loss(case: GoldCase, plan: RoutePlan | None) -> bool:
    """요청한 의도 하나가 **사용자 모르게** 사라졌나.

    - 계획이 없거나(FAILED) CLARIFY 를 냈으면 조용하지 않다 — 실패 문구나 되묻는 문구가 나간다.
    - 골드가 CLARIFY 인데 무언가를 실행·안내했으면 조용한 손실이다: 막힌 의도가 사라진 채
      다른 의도의 답만 나간다 (v1 에이전트의 clarify_07·08·12 가 이 모양이었다).
    - 골드가 실행/핸드오프인데 그 일부만 냈으면 조용한 손실이다. 아무것도 안 냈으면 FAILED
      문구가 나가므로 조용하지 않다.
    """
    if plan is None or plan.clarify is not None:
        return False
    predicted = _intents(plan)
    if case.gold_route_plan.clarify is not None:
        return bool(predicted)
    gold = _intents(case.gold_route_plan)
    return bool(predicted) and bool(gold - predicted)


def _latency_stats(latencies: Sequence[float]) -> dict[str, float | None]:
    values = sorted(latencies)
    return {
        "mean": round(statistics.fmean(values), 1) if values else None,
        "p50": round(_percentile(values, 0.50) or 0.0, 1) if values else None,
        "p95": round(_percentile(values, 0.95) or 0.0, 1) if values else None,
        "max": round(max(values), 1) if values else None,
        "count": len(values),
    }


def _token_stats(results: Sequence[CaseRun]) -> dict[str, Any]:
    inputs = sum(r.performance.input_tokens or 0 for r in results)
    outputs = sum(r.performance.output_tokens or 0 for r in results)
    total = inputs + outputs
    return {
        "input_total": inputs,
        "output_total": outputs,
        "total": total,
        "mean_per_case": round(total / len(results), 1) if results else None,
        "missing_usage_case_count": sum(
            1 for r in results if r.performance.total_tokens is None and r.error is None
        ),
    }


def _attempts_for(result: CaseRun) -> list[AttemptValidation]:
    """계획이 없는 케이스를 채점기의 언어로 옮긴다.

    `evaluate_benchmark` 는 O-14 의 재시도 계약을 강제한다 — 무효 시도 뒤에는 정확히 한 번의
    재시도가 있어야 한다. 여기서 계획이 없는 것(라우터/모델 실패 · 순수 스몰토크)은 재시도의
    문제가 아니라 "계획 없이 끝났다" 는 사실이므로, 무효 2회로 넣어 `final_schema_valid=False`
    와 `unrecovered_schema_failure_count` 가 그 사실을 세게 한다. 실제 재시도 횟수는 이 값이
    아니라 `llm_turns` 가 말한다.
    """
    if result.attempt.schema_valid:
        return [result.attempt]
    return [result.attempt, result.attempt]


def summarize_implementation(
    cases: Sequence[GoldCase], results: Sequence[CaseRun], *, prompt_version: str
) -> dict[str, Any]:
    from daengs_backend.orchestration.semantic import ROUTER_MODEL_ID

    evaluation = evaluate_benchmark(
        cases,
        {r.case_id: _attempts_for(r) for r in results},
        {r.case_id: r.performance for r in results},
        prompt_version=prompt_version,  # type: ignore[arg-type]
        model_id=ROUTER_MODEL_ID,
    )
    plans = _plans_by_case(results)
    turns = [r.turns for r in results]
    latencies = [r.performance.latency_ms for r in results if r.performance.latency_ms is not None]
    gold_by_id = {case.case_id: case for case in cases}
    return {
        "prompt_version": prompt_version,
        "summary": evaluation.summary.model_dump(mode="json"),
        "capabilities": capability_metrics(cases, plans),
        "llm_turns": {
            "total": sum(turns),
            "mean": round(statistics.fmean(turns), 2) if turns else 0,
            "max": max(turns) if turns else 0,
        },
        "tokens": _token_stats(results),
        "latency_ms": _latency_stats(latencies),
        "runner_errors": [{"case_id": r.case_id, "error": r.error} for r in results if r.error],
        "silent_intent_loss_case_ids": sorted(
            r.case_id for r in results if silent_intent_loss(gold_by_id[r.case_id], r.attempt.plan)
        ),
        "first_runner_case_count": sum(1 for r in results if r.first_runner == r.implementation),
        "status_counts": dict(Counter(r.status for r in results)),
    }


def divergence(cases: Sequence[GoldCase], runs: Mapping[str, Sequence[CaseRun]]) -> dict[str, Any]:
    """두 구현이 **의미상** 갈린 케이스와 누가 골드에 맞았나.

    `RoutePlan` 을 그대로 비교하면 `model`·`prompt_version` 때문에 전부 다르게 잡히므로
    채점기의 정규화 키로 본다 (v1 과 같다). v1 의 "계약 차이" 열은 없다 — v2 는 계약을
    맞춘 뒤의 측정이라 남는 차이는 전부 선택 차이다.
    """
    gold_by_id = {case.case_id: case for case in cases}
    plans = {name: _plans_by_case(runs[name]) for name in IMPLEMENTATIONS}
    rows: list[dict[str, Any]] = []
    favored: Counter[str] = Counter()
    for case in cases:
        keys = {n: _semantic_plan_key(plans[n].get(case.case_id)) for n in IMPLEMENTATIONS}
        if keys[IMPLEMENTATIONS[0]] == keys[IMPLEMENTATIONS[1]]:
            continue
        gold_key = _semantic_plan_key(gold_by_id[case.case_id].gold_route_plan)
        matches = {n: keys[n] == gold_key for n in IMPLEMENTATIONS}
        winners = [n for n in IMPLEMENTATIONS if matches[n]]
        favored[winners[0] if len(winners) == 1 else "neither"] += 1
        rows.append(
            {
                "case_id": case.case_id,
                "category": case.category,
                "matches_gold": matches,
                "favors": winners[0] if len(winners) == 1 else "neither",
            }
        )
    return {
        "divergent_case_count": len(rows),
        "divergent_case_ids": [row["case_id"] for row in rows],
        "favored": {name: favored[name] for name in (*IMPLEMENTATIONS, "neither")},
        "cases": rows,
    }


def summarize_repetition(
    cases: Sequence[GoldCase], meta: Mapping[str, Any], runs: Mapping[str, Sequence[CaseRun]]
) -> dict[str, Any]:
    prompt_versions = meta["settings"]["prompt_version"]
    return {
        "run": meta["run"],
        "source_sha": meta["source_sha"],
        "started_at": meta.get("started_at"),
        "warmup": meta.get("warmup"),
        "first_runner_balance": {
            name: sum(1 for r in runs[name] if r.first_runner == name) for name in IMPLEMENTATIONS
        },
        "implementations": {
            name: summarize_implementation(cases, runs[name], prompt_version=prompt_versions[name])
            for name in IMPLEMENTATIONS
        },
        "divergence": divergence(cases, runs),
    }


def stability(
    cases: Sequence[GoldCase], run_data: Sequence[Mapping[str, Sequence[CaseRun]]]
) -> dict[str, Any]:
    """같은 케이스가 세 반복에서 같은 의미 키를 냈나 — 구현별로."""
    out: dict[str, Any] = {}
    for name in IMPLEMENTATIONS:
        plans = [_plans_by_case(runs[name]) for runs in run_data]
        unstable = [
            case.case_id
            for case in cases
            if len({_semantic_plan_key(p.get(case.case_id)) for p in plans}) > 1
        ]
        out[name] = {
            "stable_case_count": len(cases) - len(unstable),
            "agreement_rate": round((len(cases) - len(unstable)) / len(cases), 4) if cases else 0.0,
            "unstable_case_ids": unstable,
        }
    return out


def _mean_over_runs(values: Sequence[float | int | None]) -> float | None:
    present = [float(v) for v in values if v is not None]
    return round(statistics.fmean(present), 4) if present else None


def _pct_diff(agent: float, langgraph: float) -> float | None:
    if not langgraph:
        return None
    return round((agent - langgraph) / langgraph * 100, 1)


_AGGREGATED_RATES = (
    "exact_route_plan_match",
    "executable_precision",
    "executable_recall",
    "exact_executable_set_accuracy_multi",
    "multi_execute_recall",
    "handoff_precision",
    "handoff_recall",
    "clarify_precision",
    "clarify_recall",
    "false_positive_clarify_rate",
    "final_schema_valid_rate",
)
_AGGREGATED_COUNTS = ("forbidden_execute_count", "invented_unsupported_capability_count")


def aggregate(
    cases: Sequence[GoldCase],
    per_run: Sequence[Mapping[str, Any]],
    run_data: Sequence[Mapping[str, Sequence[CaseRun]]],
) -> dict[str, Any]:
    impls: dict[str, Any] = {}
    for name in IMPLEMENTATIONS:
        summaries = [r["implementations"][name] for r in per_run]
        all_results = [res for runs in run_data for res in runs[name]]
        latencies = [
            r.performance.latency_ms for r in all_results if r.performance.latency_ms is not None
        ]
        impls[name] = {
            "rates_mean_over_runs": {
                key: _mean_over_runs([s["summary"][key] for s in summaries])
                for key in _AGGREGATED_RATES
            },
            "rates_per_run": {
                key: [s["summary"][key] for s in summaries] for key in _AGGREGATED_RATES
            },
            "counts_sum_over_runs": {
                key: sum(s["summary"][key] for s in summaries) for key in _AGGREGATED_COUNTS
            },
            "capabilities_mean_over_runs": {
                cap.value: {
                    metric: _mean_over_runs(
                        [s["capabilities"][cap.value][metric] for s in summaries]
                    )
                    for metric in ("precision", "recall")
                }
                for cap in CapabilityName
            },
            "llm_turns": {
                "total": sum(s["llm_turns"]["total"] for s in summaries),
                "mean_per_case": _mean_over_runs([s["llm_turns"]["mean"] for s in summaries]),
                "max": max(s["llm_turns"]["max"] for s in summaries),
            },
            "tokens": {
                "input_total": sum(s["tokens"]["input_total"] for s in summaries),
                "output_total": sum(s["tokens"]["output_total"] for s in summaries),
                "total": sum(s["tokens"]["total"] for s in summaries),
                "mean_per_case": _mean_over_runs([s["tokens"]["mean_per_case"] for s in summaries]),
                "per_run_total": [s["tokens"]["total"] for s in summaries],
            },
            "latency_ms_pooled": _latency_stats(latencies),
            "latency_ms_per_run": [s["latency_ms"] for s in summaries],
            "runner_error_count": sum(len(s["runner_errors"]) for s in summaries),
            "silent_intent_loss": dict(
                Counter(case_id for s in summaries for case_id in s["silent_intent_loss_case_ids"])
            ),
            "first_runner_case_count_per_run": [s["first_runner_case_count"] for s in summaries],
        }

    lg, ag = impls["langgraph"], impls["agent"]
    divergent_per_run = [set(r["divergence"]["divergent_case_ids"]) for r in per_run]
    favored_total: Counter[str] = Counter()
    for r in per_run:
        favored_total.update(r["divergence"]["favored"])
    return {
        "implementations": impls,
        "cost_difference_agent_vs_langgraph_pct": {
            "total_tokens": _pct_diff(ag["tokens"]["total"], lg["tokens"]["total"]),
            "input_tokens": _pct_diff(ag["tokens"]["input_total"], lg["tokens"]["input_total"]),
            "output_tokens": _pct_diff(ag["tokens"]["output_total"], lg["tokens"]["output_total"]),
            "llm_turns": _pct_diff(ag["llm_turns"]["total"], lg["llm_turns"]["total"]),
            "mean_latency_ms": _pct_diff(
                ag["latency_ms_pooled"]["mean"] or 0.0, lg["latency_ms_pooled"]["mean"] or 0.0
            ),
        },
        "stability": stability(cases, run_data),
        "divergence": {
            "in_any_run": sorted(set().union(*divergent_per_run)) if divergent_per_run else [],
            "in_every_run": sorted(set.intersection(*divergent_per_run))
            if divergent_per_run
            else [],
            "favored_sum_over_runs": {
                name: favored_total[name] for name in (*IMPLEMENTATIONS, "neither")
            },
        },
    }


def recommendation(agg: Mapping[str, Any], per_run: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """규칙으로 정한 권고. 판단은 사람이 한다 — 이건 그 판단이 볼 숫자를 한 줄로 줄인 것이다.

    - 모든 반복에서 한쪽의 완전 일치가 다른 쪽 이상이고 토큰 합계가 다른 쪽 이하면 그쪽을 남긴다.
    - 반복 사이에 부호가 갈리거나 정확도와 비용이 서로 반대면 `inconclusive` — D-055 ⑥ 은 그때
      기본값인 LangGraph 를 남기기로 이미 정해 두었다.
    - 러너 오류·금지 실행·지어낸 능력이 있으면 어느 쪽이든 권고를 보류한다.
    """
    lg = agg["implementations"]["langgraph"]
    ag = agg["implementations"]["agent"]
    exact_lg = lg["rates_per_run"]["exact_route_plan_match"]
    exact_ag = ag["rates_per_run"]["exact_route_plan_match"]
    tokens_lg = lg["tokens"]["per_run_total"]
    tokens_ag = ag["tokens"]["per_run_total"]
    blockers = [
        name
        for name, impl in (("langgraph", lg), ("agent", ag))
        if impl["runner_error_count"]
        or impl["counts_sum_over_runs"]["forbidden_execute_count"]
        or impl["counts_sum_over_runs"]["invented_unsupported_capability_count"]
    ]
    if blockers:
        return {
            "verdict": "inconclusive",
            "reason": f"runner errors or contract gate hits: {blockers}",
        }
    if exact_lg == exact_ag and tokens_lg == tokens_ag:
        return {
            "verdict": "inconclusive",
            "reason": "exact match and total tokens tie in every run (D-055 ⑥ → keep the default)",
        }
    lg_wins = all(a >= b for a, b in zip(exact_lg, exact_ag, strict=True)) and all(
        a <= b for a, b in zip(tokens_lg, tokens_ag, strict=True)
    )
    ag_wins = all(b > a for a, b in zip(exact_lg, exact_ag, strict=True)) and all(
        b <= a for a, b in zip(tokens_lg, tokens_ag, strict=True)
    )
    if lg_wins:
        return {
            "verdict": "retain_langgraph",
            "reason": "exact match ≥ agent and total tokens ≤ agent in every run",
        }
    if ag_wins:
        return {
            "verdict": "retain_agent",
            "reason": "exact match > langgraph and total tokens ≤ langgraph in every run",
        }
    return {
        "verdict": "inconclusive",
        "reason": "accuracy and cost do not point the same way in every run (D-055 ⑥ → keep the default)",
    }


def build_summary(
    cases: Sequence[GoldCase],
    loaded: Sequence[tuple[Mapping[str, Any], Mapping[str, Sequence[CaseRun]]]],
) -> dict[str, Any]:
    metas = [meta for meta, _ in loaded]
    shas = {meta["source_sha"] for meta in metas}
    if len(shas) != 1:
        raise ValueError(f"runs come from different source commits: {sorted(shas)}")
    settings_seen = [json.dumps(meta["settings"], sort_keys=True) for meta in metas]
    if len(set(settings_seen)) != 1:
        raise ValueError("runs were recorded with different controlled settings")
    for meta, runs in loaded:
        for name in IMPLEMENTATIONS:
            ids = [r.case_id for r in runs[name]]
            if ids != [case.case_id for case in cases]:
                raise ValueError(f"run {meta['run']} {name}: case set differs from gold")

    per_run = [summarize_repetition(cases, meta, runs) for meta, runs in loaded]
    agg = aggregate(cases, per_run, [runs for _, runs in loaded])
    return {
        "benchmark_id": BENCHMARK_ID,
        "card": CARD,
        "decision": DECISION,
        "gold_file": GOLD_FILE,
        "scored_case_count": len(cases),
        "run_count": len(loaded),
        "explicit_signal_case_ids": sorted(_explicit_signal_case_ids(list(cases))),
        "benchmark_source_sha": metas[0]["source_sha"],
        "source_dirty_files": metas[0].get("source_dirty_files"),
        "provenance": metas[0].get("provenance"),
        "settings": metas[0]["settings"],
        "runs": per_run,
        "aggregate": agg,
        "recommendation": recommendation(agg, per_run),
        "real_tester_holdout": {
            "status": "pending",
            "reason": "no de-identified real tester-query dataset exists in the repository",
        },
    }


# ---------------------------------------------------------------------------
# 리포트
# ---------------------------------------------------------------------------


_REPORT_ROWS = (
    ("RoutePlan 완전 일치", "exact_route_plan_match"),
    ("실행 precision", "executable_precision"),
    ("실행 recall", "executable_recall"),
    ("다중 실행 집합 정확도", "exact_executable_set_accuracy_multi"),
    ("핸드오프 precision", "handoff_precision"),
    ("핸드오프 recall", "handoff_recall"),
    ("CLARIFY precision", "clarify_precision"),
    ("CLARIFY recall", "clarify_recall"),
    ("헛 CLARIFY 비율", "false_positive_clarify_rate"),
    ("스키마 유효율", "final_schema_valid_rate"),
    ("금지 실행 건수", "forbidden_execute_count"),
    ("지어낸 능력 건수", "invented_unsupported_capability_count"),
)


def _fmt(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return "–"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _markdown_report(summary: Mapping[str, Any]) -> str:
    runs = summary["runs"]
    agg = summary["aggregate"]
    impls = agg["implementations"]
    prov = summary.get("provenance") or {}
    settings = summary["settings"]

    header = (
        "| 지표 | "
        + " | ".join(f"{name} r{r['run']}" for r in runs for name in IMPLEMENTATIONS)
        + " |"
    )
    sep = "| --- | " + " | ".join("---" for _ in runs for _ in IMPLEMENTATIONS) + " |"
    rows = [header, sep]
    for label, key in _REPORT_ROWS:
        cells = [
            _fmt(r["implementations"][n]["summary"][key]) for r in runs for n in IMPLEMENTATIONS
        ]
        rows.append(f"| {label} | " + " | ".join(cells) + " |")
    for cap in CapabilityName:
        for metric in ("precision", "recall"):
            cells = [
                _fmt(r["implementations"][n]["capabilities"][cap.value][metric])
                for r in runs
                for n in IMPLEMENTATIONS
            ]
            rows.append(f"| {cap.value} {metric} | " + " | ".join(cells) + " |")
    for label, path in (
        ("LLM 턴 합계", ("llm_turns", "total")),
        ("LLM 턴 평균", ("llm_turns", "mean")),
        ("LLM 턴 최대", ("llm_turns", "max")),
        ("입력 토큰 합계", ("tokens", "input_total")),
        ("출력 토큰 합계", ("tokens", "output_total")),
        ("토큰 합계", ("tokens", "total")),
        ("케이스당 토큰 평균", ("tokens", "mean_per_case")),
        ("지연 평균(ms)", ("latency_ms", "mean")),
        ("지연 p50(ms)", ("latency_ms", "p50")),
        ("지연 p95(ms)", ("latency_ms", "p95")),
        ("지연 최대(ms)", ("latency_ms", "max")),
        ("러너 오류", ("runner_errors",)),
        ("조용한 의도 손실", ("silent_intent_loss_case_ids",)),
        ("먼저 돈 케이스 수", ("first_runner_case_count",)),
    ):
        cells = []
        for r in runs:
            for n in IMPLEMENTATIONS:
                value: Any = r["implementations"][n]
                for part in path:
                    value = value[part]
                cells.append(str(len(value)) if isinstance(value, list) else _fmt(value))
        rows.append(f"| {label} | " + " | ".join(cells) + " |")
    per_run_table = "\n".join(rows)

    agg_rows = ["| 지표 (3회) | langgraph | agent |", "| --- | --- | --- |"]
    for label, key in _REPORT_ROWS:
        if key in _AGGREGATED_RATES:
            agg_rows.append(
                f"| {label} 평균 | "
                + " | ".join(_fmt(impls[n]["rates_mean_over_runs"][key]) for n in IMPLEMENTATIONS)
                + " |"
            )
        else:
            agg_rows.append(
                f"| {label} 합계 | "
                + " | ".join(_fmt(impls[n]["counts_sum_over_runs"][key]) for n in IMPLEMENTATIONS)
                + " |"
            )
    for label, path in (
        ("LLM 턴 합계", ("llm_turns", "total")),
        ("LLM 턴 케이스 평균", ("llm_turns", "mean_per_case")),
        ("토큰 합계", ("tokens", "total")),
        ("입력 토큰 합계", ("tokens", "input_total")),
        ("출력 토큰 합계", ("tokens", "output_total")),
        ("케이스당 토큰 평균", ("tokens", "mean_per_case")),
        ("지연 평균(ms, 240건 합산)", ("latency_ms_pooled", "mean")),
        ("지연 p50(ms)", ("latency_ms_pooled", "p50")),
        ("지연 p95(ms)", ("latency_ms_pooled", "p95")),
        ("지연 최대(ms)", ("latency_ms_pooled", "max")),
        ("러너 오류 합계", ("runner_error_count",)),
    ):
        cells = []
        for n in IMPLEMENTATIONS:
            value: Any = impls[n]
            for part in path:
                value = value[part]
            cells.append(_fmt(value))
        agg_rows.append(f"| {label} | " + " | ".join(cells) + " |")
    aggregate_table = "\n".join(agg_rows)

    pct = agg["cost_difference_agent_vs_langgraph_pct"]
    stab = agg["stability"]
    div = agg["divergence"]

    div_rows = [
        "| 반복 | 갈린 케이스 | langgraph 우세 | agent 우세 | 둘 다 틀림 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in runs:
        d = r["divergence"]
        div_rows.append(
            f"| r{r['run']} | {', '.join(f'`{c}`' for c in d['divergent_case_ids']) or '없음'} | "
            f"{d['favored']['langgraph']} | {d['favored']['agent']} | {d['favored']['neither']} |"
        )
    divergence_table = "\n".join(div_rows)

    def _silent(name: str) -> str:
        items = impls[name]["silent_intent_loss"]
        return ", ".join(f"`{c}`×{k}" for c, k in sorted(items.items())) or "없음"

    settings_rows = ["| 설정 | langgraph | agent |", "| --- | --- | --- |"]
    for key, value in settings.items():
        if isinstance(value, dict) and set(value) == set(IMPLEMENTATIONS):
            settings_rows.append(f"| {key} | {value['langgraph']} | {value['agent']} |")
        else:
            settings_rows.append(f"| {key} | {value} | (같음) |")
    settings_table = "\n".join(settings_rows)

    packages = ", ".join(f"{k} {v}" for k, v in (prov.get("packages") or {}).items())
    rec = summary["recommendation"]
    warmups = "; ".join(
        f"r{r['run']}: "
        + ", ".join(
            f"{n} {v['status']} {v['latency_ms']}ms"
            for n, v in (r.get("warmup") or {}).get("results", {}).items()
        )
        for r in runs
    )
    balance = "; ".join(
        f"r{r['run']}: " + ", ".join(f"{n} {c}" for n, c in r["first_runner_balance"].items())
        for r in runs
    )
    dirty = summary.get("source_dirty_files") or []

    return f"""# 오케스트레이터 비교 v2 ({CARD} · {DECISION} ⑥)

> 이 실험은 **같은 DAENGS v1 오케스트레이션 계약** 아래에서 planner-first 결정론 워크플로우와
> 반복 툴 선택 루프를 비교한다. LangGraph 와 LangChain 을 서로 배타적인 런타임 기술로 비교하는
> 것이 아니다 — `create_agent` 자체가 LangGraph 런타임 위에서 돈다.

- 골드: `{summary["gold_file"]}` · 채점 케이스 {summary["scored_case_count"]}개 · 반복 {summary["run_count"]}회
- `benchmark_source_sha`: `{summary["benchmark_source_sha"]}`{" ⚠ dirty: " + ", ".join(dirty) if dirty else ""}
- `dev` 소스 SHA: `{prov.get("dev_source_sha", "unknown")}`
- Agent 구현 SHA: `{prov.get("agent_implementation_sha", "unknown")}`
- LangGraph 구현 SHA: `{prov.get("langgraph_implementation_sha", "unknown")}`
- 패키지: {packages or "unknown"}
- 채점기: `daengs_evals/router_benchmark/evaluate.py` (v1~v8 · 비교 v1 과 **같은 자**)
- 어댑터: 가짜(즉시 OK) — 재는 것은 능력 선택 · 계약 준수 · 오케스트레이션 오버헤드다.
  Training RAG · Life 답 품질 · Place HTTP · DB 지연 · 운영 end-to-end 지연은 재지 않는다
- 예열(비채점): {warmups or "없음"}
- 선행 구현 균형(실측): {balance}

## 통제 설정

{settings_table}

구조화 출력 대 툴 호출에서 **피할 수 없는 차이**는 같다고 적지 않는다: 라우터는 JSON 스키마로
강제한 출력 한 번(스키마 실패 시 1회 재시도), 에이전트는 턴마다 function-call 파트와 마무리
문장(재시도 없음, `recursion_limit` 안전장치). 프롬프트는 형태·언어가 다르다. 두 구현이 보는
입력(질문 + 라우팅 메타데이터)과 그 뒤의 게이트·payload·실행·집계는 같은 코드다.

## 실행별 지표

{per_run_table}

## 3회 합산

{aggregate_table}

비용 차이 (agent − langgraph, langgraph 대비 %): 토큰 합계 {pct["total_tokens"]}% ·
입력 {pct["input_tokens"]}% · 출력 {pct["output_tokens"]}% · LLM 턴 {pct["llm_turns"]}% ·
평균 지연 {pct["mean_latency_ms"]}%. **토큰은 측정값이고 턴 수에서 추정하지 않았다.**

## 안정성 — 같은 케이스가 세 번 같은 답을 냈나

| 구현 | 안정 케이스 | 일치율 | 불안정 케이스 |
| --- | --- | --- | --- |
{chr(10).join(f"| {n} | {stab[n]['stable_case_count']}/{summary['scored_case_count']} | {stab[n]['agreement_rate']:.3f} | {', '.join(f'`{c}`' for c in stab[n]['unstable_case_ids']) or '없음'} |" for n in IMPLEMENTATIONS)}

## 두 구현이 갈린 곳

{divergence_table}

- 어느 반복에서든 갈린 케이스: {", ".join(f"`{c}`" for c in div["in_any_run"]) or "없음"}
- 세 반복 모두에서 갈린 케이스: {", ".join(f"`{c}`" for c in div["in_every_run"]) or "없음"}
- 우세 합계(3회): langgraph {div["favored_sum_over_runs"]["langgraph"]} · agent {div["favored_sum_over_runs"]["agent"]} · 둘 다 틀림 {div["favored_sum_over_runs"]["neither"]}

## 조용한 의도 손실

요청한 의도가 사용자 모르게 사라진 케이스(× 반복 횟수). 골드가 CLARIFY 인데 무언가를
실행했거나, 골드 의도의 일부만 낸 경우다. FAILED 나 CLARIFY 로 끝난 것은 조용하지 않다.

- langgraph: {_silent("langgraph")}
- agent: {_silent("agent")}

## 실패 계약

가짜 어댑터는 항상 OK 라 여기서는 안 보인다. 오류·타임아웃·혼합·계획 동결 전 모델 실패는
`tests/test_orchestrator_failure_contract.py` 가 두 구현에 같은 시나리오를 먹여 결정론으로
검증한다. 이 80케이스 점수와는 섞이지 않는다. 계약이 모호한 자리는 그 테스트의 docstring 에
적어 두었고 새 규칙을 만들지 않았다.

## 실사용 테스터 홀드아웃

**보류.** 저장소에 비식별 실사용 테스터 질의 세트가 없다 (`evals/` 에는 사람이 작성한 골드와
결과만 있고, 실사용 신호는 `docs/life/roadmap.md` 기준 채팅 8턴이다). 만들지 않았다.
기대 스키마와 권장 구성은 `evals/orchestration_router/README.md` 의 v2 절에 있다.

## 지지되는 결론과 지지되지 않는 결론

**지지되는 것** — 같은 계약·같은 모델·같은 설정·같은 어댑터·같은 채점기로 80케이스를 3회
잰 값이다. 위 표의 정확도 차이, 토큰·지연 차이, 안정성, 갈린 케이스는 이 데이터가 말한다.

**지지되지 않는 것** — ⑴ 실사용 질의에서의 우열(홀드아웃 없음). ⑵ 진짜 어댑터를 문
end-to-end 지연·품질. ⑶ 통계적 유의성 — 세 반복은 같은 80케이스의 종속 관측이라 합쳐서
검정하지 않았다. ⑷ LangGraph 와 LangChain 이라는 기술의 우열 — 잰 것은 두 오케스트레이션
패턴이다. ⑸ 에이전트가 툴 결과를 보고 다음 수를 정하는 능력 — 이 계약에서는 그 능력을
쓰지 않는다.

## 권고

`{rec["verdict"]}` — {rec["reason"]}

권고 규칙은 `runner_v2.recommendation` 에 적혀 있다. 결론을 못 내면 D-055 ⑥ 대로 기본값인
LangGraph 를 남긴다. **이 PR 에서는 어느 구현도 지우지 않는다.**

## 사람의 결정

<!-- 어느 구현을 남길지. 사람이 v2 를 보고 정한 뒤 D-055 ⑥ 에 반영하고, 진 쪽을 지우는 카드를 연다. -->
"""


def write_summary(summary: Mapping[str, Any]) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    REPORT_PATH.write_text(_markdown_report(summary), encoding="utf-8")


# ---------------------------------------------------------------------------


async def execute_run(cases: Sequence[GoldCase], run: int, *, limit: int | None) -> Path:
    provenance = source_provenance()
    meters = {name: Meter() for name in IMPLEMENTATIONS}
    sink: dict[str, Any] = {"plan": None}
    pair = build_pair(meters, sink)
    scored = list(cases)[:limit] if limit else list(cases)
    print(f"반복 {run}: 골드 {len(scored)}개 × 구현 {len(IMPLEMENTATIONS)}개 · 예열 먼저")
    warm = await warm_up(pair, meters, sink, cases[WARMUP_CASE_INDEX], run=run)
    runs = await run_repetition(scored, run, pair, meters, sink)
    meta = {
        "benchmark_id": BENCHMARK_ID,
        "card": CARD,
        "run": run,
        "source_sha": provenance["benchmark_source_sha"],
        "source_dirty_files": provenance["source_dirty_files"],
        "gold_file": GOLD_FILE,
        "case_count": len(scored),
        "limited": bool(limit),
        "started_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "warmup": warm,
        "settings": controlled_settings(),
        "provenance": provenance,
    }
    path = (
        results_path(run) if not limit else RESULTS_DIR / f"comparison_v2_smoke_run_{run:02d}.jsonl"
    )
    write_run(path, scored, runs, meta=meta)
    print(f"결과  {path}")
    return path


def summarize(cases: Sequence[GoldCase]) -> dict[str, Any]:
    loaded = [load_run(results_path(run)) for run in RUN_NUMBERS]
    summary = build_summary(cases, loaded)
    write_summary(summary)
    print(f"요약  {SUMMARY_PATH}")
    print(f"리포트 {REPORT_PATH}")
    for name in IMPLEMENTATIONS:
        impl = summary["aggregate"]["implementations"][name]
        print(
            f"  {name:<10} exact(mean)={impl['rates_mean_over_runs']['exact_route_plan_match']} "
            f"tokens={impl['tokens']['total']} latency(mean)={impl['latency_ms_pooled']['mean']}ms "
            f"errors={impl['runner_error_count']}"
        )
    print(f"  권고: {summary['recommendation']['verdict']}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="두 오케스트레이터 비교 v2 (#272)")
    parser.add_argument("--run", type=int, choices=RUN_NUMBERS, help="반복 번호 하나를 실행")
    parser.add_argument("--all", action="store_true", help="반복 1·2·3 을 이어서 실행하고 요약")
    parser.add_argument("--summarize", action="store_true", help="저장된 반복 셋에서 요약·리포트")
    parser.add_argument(
        "--limit", type=int, default=None, help="앞에서 N개만 (연기 시험, 별도 파일)"
    )
    args = parser.parse_args()

    cases = list(load_gold_cases())
    if args.run:
        asyncio.run(execute_run(cases, args.run, limit=args.limit))
    elif args.all:
        for run in RUN_NUMBERS:
            asyncio.run(execute_run(cases, run, limit=args.limit))
        if not args.limit:
            summarize(cases)
    elif args.summarize:
        summarize(cases)
    else:
        parser.error("--run N, --all, --summarize 중 하나")


if __name__ == "__main__":
    main()
