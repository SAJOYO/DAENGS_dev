"""동결 질문을 오케스트레이터에 먹여 답을 모은다 (#277).

    uv run python -m tools.answer_quality.collect --flag off --adapters fake --limit 10 --label smoke_off
    uv run python -m tools.answer_quality.collect --flag off --adapters real --label off
    uv run python -m tools.answer_quality.collect --flag on  --adapters fallback-only --label on

오케스트레이터는 비교 v2 의 LangGraph 쪽과 **같은 조립**이다 — `AssistantOrchestrationService`
에 진짜 의미 라우터(토큰을 세는 transport)와 계획을 잡는 `RecordingEngine` 을 문다. 갈리는 것은
어댑터뿐이다:

    real            엔진 기본값 — 운영 어댑터 전부 (개발 PC 에서 서버 DB · Redis 가 닿아야 한다)
    fake            비교 러너의 즉시 OK 가짜 — 라우팅 · 문구 · 상태 단계만 잰다
    fallback-only   `general` 만 진짜(#279 의 어댑터), 나머지는 가짜 — 폴백 답의 품질만 잰다

`--flag on|off` 는 `settings.general_fallback` 을 **이 프로세스 안에서** 바꾼다 (#279 가 더하는
설정). 그 속성이 아직 없는 브랜치에서는 `off` 만 받는다 — 폴백이 없는 코드는 곧 꺼진 코드다.
`on` 은 속성이 없으면 명확히 실패한다.

결과 파일 `answers_<label>.jsonl` 은 meta 행 하나 뒤에 질문마다 한 행이다. 답변률의 정의(FAILED ·
CLARIFY · RUNNER_ERROR 가 아닌 비율)는 리포트가 상태에서 계산하고 여기서는 상태만 적는다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from daengs_backend.orchestration.contracts import CapabilityName, PrincipalContext
from tools.answer_quality.gemini import DEFAULT_TOKEN_BUDGET, TokenBudgetExceeded, TokenLedger
from tools.answer_quality.provenance import source_provenance, utc_now
from tools.answer_quality.questions import (
    ASSETS_DIR,
    QUESTIONS_V1_PATH,
    QuestionCase,
    file_sha256,
    load_questions,
)
from tools.answer_quality.strata import resolve_strata
from tools.orchestrator_comparison.runner import Meter, _fake_adapters
from tools.orchestrator_comparison.runner_v2 import RecordingEngine, _metered_semantic_generate

BENCHMARK_ID = "answer-quality-v1"
CARD = "#277"
FLAG_SETTING = "general_fallback"
AdapterMode = Literal["real", "fake", "fallback-only"]
ADAPTER_MODES: tuple[AdapterMode, ...] = ("real", "fake", "fallback-only")
#: 답으로 치지 않는 상태. FAILED · CLARIFY 는 PR 본문의 정의, RUNNER_ERROR 는 러너 자체의 예외다.
NOT_ANSWERED_STATUSES = frozenset({"FAILED", "CLARIFY", "RUNNER_ERROR"})

_PRINCIPAL = PrincipalContext(subject="answer-quality-runner", kind="ADMIN")


def answers_path(label: str) -> Path:
    return ASSETS_DIR / f"answers_{label}.jsonl"


# ---------------------------------------------------------------------------
# 플래그 · 어댑터
# ---------------------------------------------------------------------------


def apply_flag(settings_obj: Any, flag: str) -> dict[str, Any]:
    """`settings.general_fallback` 을 이 프로세스에서 켜거나 끈다.

    속성이 없는 브랜치(#279 이전)에서는 `off` 만 통과한다 — 폴백 코드가 없으니 동작이 곧 off 다.
    `on` 은 조용히 무시하면 "폴백 후" 라고 적힌 파일이 "전" 을 담게 되므로 여기서 멈춘다.
    """
    if flag not in ("on", "off"):
        raise ValueError(f"--flag 는 on|off 입니다: {flag!r}")
    present = hasattr(settings_obj, FLAG_SETTING)
    if not present:
        if flag == "on":
            raise RuntimeError(
                f"settings.{FLAG_SETTING} 이 이 브랜치에 없습니다 (#279 가 더하는 설정). "
                "--flag on 은 #279 브랜치를 머지하거나 그 위에서 돌리세요."
            )
        return {"requested": flag, "setting_present": False, "effective": "off (absent)"}
    setattr(settings_obj, FLAG_SETTING, flag == "on")
    return {"requested": flag, "setting_present": True, "effective": flag}


def _general_adapter() -> Any:
    """#279 의 일반 폴백 어댑터. 이 브랜치에 없으면 무엇이 없는지 말하고 멈춘다."""
    capability = getattr(CapabilityName, "GENERAL", None)
    if capability is None:
        raise RuntimeError(
            "CapabilityName.GENERAL 이 없습니다 (#279). fallback-only 는 그 브랜치 위에서만 돕니다."
        )
    try:
        from daengs_backend.orchestration.adapters import general as general_mod
    except ImportError as exc:
        raise RuntimeError(
            "daengs_backend.orchestration.adapters.general 을 import 하지 못했습니다 (#279)."
        ) from exc
    for value in vars(general_mod).values():
        if isinstance(value, type) and getattr(value, "capability", None) == capability:
            return value()
    raise RuntimeError("adapters.general 에 capability=GENERAL 인 어댑터 클래스가 없습니다 (#279).")


def build_adapters(mode: str) -> Mapping[CapabilityName, Any] | None:
    """어댑터 셋. `None` 은 엔진 기본값(운영 어댑터)이다."""
    if mode == "real":
        return None
    if mode == "fake":
        return _fake_adapters()
    if mode == "fallback-only":
        adapters: dict[CapabilityName, Any] = dict(_fake_adapters())
        general = _general_adapter()
        adapters[general.capability] = general
        return adapters
    raise ValueError(f"--adapters 는 {'|'.join(ADAPTER_MODES)} 입니다: {mode!r}")


def build_orchestrator(mode: str, meter: Meter, sink: dict[str, Any]) -> Any:
    """비교 v2 `build_pair` 의 LangGraph 쪽과 같은 조립. 환경 변수를 토글하지 않는다."""
    from daengs_backend.orchestration.semantic import GeminiSemanticRouter
    from daengs_backend.orchestration.service import AssistantOrchestrationService

    return AssistantOrchestrationService(
        engine=RecordingEngine(build_adapters(mode), sink),  # type: ignore[arg-type]
        semantic_router=GeminiSemanticRouter(generate=_metered_semantic_generate(meter)),
    )


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------


def select_questions(
    cases: Sequence[QuestionCase], *, strata: list[str] | None, limit: int | None
) -> list[QuestionCase]:
    allowed = {s.id for s in resolve_strata(strata)}
    chosen = [case for case in cases if case.stratum in allowed]
    return chosen[:limit] if limit else chosen


async def run_question(
    orchestrator: Any, case: QuestionCase, meter: Meter, sink: dict[str, Any]
) -> dict[str, Any]:
    """질문 하나. 실패해도 다음으로 넘어가되 RUNNER_ERROR 로 남긴다."""
    meter.reset()
    sink["plan"] = None
    started = time.perf_counter()
    row: dict[str, Any] = {
        "kind": "answer",
        "question_id": case.question_id,
        "stratum": case.stratum,
        "status": "RUNNER_ERROR",
        "message": "",
        "results": [],
        "handoffs": [],
        "clarify": None,
        "plan": None,
        "error": None,
    }
    try:
        response = await orchestrator.run(
            query=case.query, principal=_PRINCIPAL, context=dict(case.context)
        )
        row.update(
            status=response.status.value,
            message=response.message,
            results=[r.model_dump(mode="json") for r in response.results],
            handoffs=[h.model_dump(mode="json") for h in response.handoffs],
            clarify=response.clarify.model_dump(mode="json") if response.clarify else None,
        )
    except Exception as exc:  # noqa: BLE001 - 한 질문의 실패가 수집 전체를 멈추지 않는다
        row["error"] = f"{type(exc).__name__}: {exc}"
    plan = sink["plan"]
    row["plan"] = plan.model_dump(mode="json") if plan is not None else None
    row["latency_ms"] = round((time.perf_counter() - started) * 1_000, 1)
    row["router_turns"] = meter.turns
    row["input_tokens"] = meter.input_tokens or None
    row["output_tokens"] = meter.output_tokens or None
    return row


async def collect(
    cases: Sequence[QuestionCase],
    orchestrator: Any,
    meter: Meter,
    sink: dict[str, Any],
    *,
    ledger: TokenLedger,
    log: Callable[[str], None] = print,
) -> tuple[list[dict[str, Any]], str | None]:
    """순차 실행. 예산을 넘으면 거기까지의 행과 멈춘 이유를 돌려준다."""
    rows: list[dict[str, Any]] = []
    stopped: str | None = None
    for index, case in enumerate(cases, start=1):
        row = await run_question(orchestrator, case, meter, sink)
        rows.append(row)
        marker = "ok" if row["error"] is None else "ERR"
        log(
            f"  [{index:>3}/{len(cases)}] {case.question_id:<40} {marker} "
            f"status={row['status']:<9} turns={row['router_turns']} {row['latency_ms']:.0f}ms"
            + (f"  {row['error']}" if row["error"] else "")
        )
        try:
            ledger.add(
                input_tokens=meter.input_tokens,
                output_tokens=meter.output_tokens,
                label=case.question_id,
            )
        except TokenBudgetExceeded as exc:
            stopped = str(exc)
            log(f"중단: {exc}")
            break
    return rows, stopped


# ---------------------------------------------------------------------------
# 저장 · 적재
# ---------------------------------------------------------------------------


def controlled_settings(adapter_mode: str, flag: Mapping[str, Any]) -> dict[str, Any]:
    from daengs_backend.config import settings
    from daengs_backend.orchestration.semantic import (
        PROMPT_VERSION,
        ROUTER_MAX_OUTPUT_TOKENS,
        ROUTER_MODEL_ID,
        ROUTER_TEMPERATURE,
    )

    return {
        "orchestrator": "langgraph (AssistantOrchestrationService + RecordingEngine)",
        "router_model_id": ROUTER_MODEL_ID,
        "router_prompt_version": PROMPT_VERSION,
        "router_temperature": ROUTER_TEMPERATURE,
        "router_max_output_tokens": ROUTER_MAX_OUTPUT_TOKENS,
        "provider_timeout_ms": settings.gemini_timeout_ms,
        "adapters": adapter_mode,
        "general_fallback": dict(flag),
        "principal": f"{_PRINCIPAL.kind} {_PRINCIPAL.subject}",
        "locale": "ko-KR",
        "tokens_scope": "semantic router calls only (adapter-internal model calls are not metered)",
        "credential": "settings.gemini_api_key — never printed",
    }


def write_answers(
    path: Path, rows: Sequence[Mapping[str, Any]], *, meta: Mapping[str, Any]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({"kind": "meta", **meta}, ensure_ascii=False) + "\n")
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_answers(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    meta: dict[str, Any] | None = None
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("kind") == "meta":
            meta = row
        else:
            rows.append(row)
    if meta is None:
        raise ValueError(f"{path} has no meta row")
    return meta, rows


def status_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(str(row["status"]) for row in rows).items()))


# ---------------------------------------------------------------------------


def main() -> None:
    from daengs_backend.config import settings

    parser = argparse.ArgumentParser(description="답변 수집 러너 (#277)")
    parser.add_argument("--flag", choices=("on", "off"), required=True)
    parser.add_argument("--adapters", choices=ADAPTER_MODES, required=True)
    parser.add_argument("--label", required=True, help="answers_<label>.jsonl")
    parser.add_argument("--questions", type=Path, default=QUESTIONS_V1_PATH)
    parser.add_argument("--strata", nargs="*", help="계층 id · 주제 · 문체 이름으로 거른다")
    parser.add_argument("--limit", type=int, default=None, help="앞에서 N개만 (연기 시험)")
    parser.add_argument("--token-budget", type=int, default=DEFAULT_TOKEN_BUDGET)
    args = parser.parse_args()

    flag = apply_flag(settings, args.flag)
    cases = select_questions(load_questions(args.questions), strata=args.strata, limit=args.limit)
    meter = Meter()
    sink: dict[str, Any] = {"plan": None}
    orchestrator = build_orchestrator(args.adapters, meter, sink)
    ledger = TokenLedger(budget=args.token_budget, log=print)
    print(
        f"질문 {len(cases)}건 · 어댑터 {args.adapters} · 플래그 {flag['effective']} · "
        f"라벨 {args.label}"
    )
    started = utc_now()
    rows, stopped = asyncio.run(collect(cases, orchestrator, meter, sink, ledger=ledger))
    meta = {
        "benchmark_id": BENCHMARK_ID,
        "card": CARD,
        "label": args.label,
        "questions_file": args.questions.name,
        "questions_sha256": file_sha256(args.questions),
        "question_count": len(rows),
        "requested_count": len(cases),
        "limited": bool(args.limit),
        "strata_filter": args.strata or None,
        "started_at": started,
        "finished_at": utc_now(),
        "stopped": stopped,
        "status_counts": status_counts(rows),
        "settings": controlled_settings(args.adapters, flag),
        "tokens": ledger.summary(),
        **source_provenance(),
    }
    path = answers_path(args.label)
    write_answers(path, rows, meta=meta)
    print(
        f"토큰 합계 {ledger.total:,} (입력 {ledger.input_tokens:,} · 출력 {ledger.output_tokens:,})"
    )
    print(f"상태  {meta['status_counts']}")
    print(f"결과  {path}")


if __name__ == "__main__":
    main()
