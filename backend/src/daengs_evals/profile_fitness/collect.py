"""질문 × 프로필 행렬을 **셀** 단위로 수집한다.

    uv run python -m daengs_evals.profile_fitness.collect --condition all --label pf_v1
    uv run python -m daengs_evals.profile_fitness.collect --condition noise --label pf_v1 --limit 5
    uv run python -m daengs_evals.profile_fitness.collect --condition all --label pf_v1 --resume

셀 = (question_id, arm, run). 쌍이 아니라 셀로 적는 이유 셋: arm 을 더해도 재수집이 필요 없고,
한 셀이 실패해도 나머지 쌍이 살고, 쌍 만들기(`pairs.py`)가 순수 함수가 되어 모델 없이 테스트할
수 있다.

조건 셋이 어떤 셀을 만드는가:

    contrast   arms[0] run 0 · arms[1] run 0    — 본 비교 (reactive → 반응성, invariant → 불변성)
    ablation   "none" run 0                     — reactive 만. 프로필 없음 vs 있음 (조건 P)
    noise      arms[0] run 1                    — 같은 프로필 한 번 더 (조건 N, 잡음 바닥)

**`--flag on` 이 기본이고 사실상 필수다.** 실측(2026-09-09)에서 `general` 폴백이 꺼져 있으면
일상 돌봄 질문이 **전부** FAILED("반려견에 관한 질문만…") 였다. 끄고 돌리면 조건 N/P/S 가 통째로
FAILED 로 채워진다.

**재개된다.** 같은 label 파일에 이미 있는 셀은 건너뛴다 — 2시간짜리가 90% 에서 죽어도 처음부터
돌리지 않는다. 다만 meta 의 질문 · 프로필 해시가 다르면 거부한다: 다른 입력으로 만든 셀에 이어
붙이면 한 파일 안에서 실험이 갈린다.

**토큰은 두 곳을 센다.** 의미 라우터(`Meter`)와, `answer_quality/collect.py` 가 못 세던 **general
어댑터 자신의 Gemini 호출**(`_metered_general_generate`). 어댑터가 `generate=` 를 주입받으므로
운영 코드를 한 줄도 안 바꾸고 센다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from daengs_backend.orchestration.contracts import CapabilityName, PrincipalContext
from daengs_evals.answer_quality.collect import (
    Meter,
    RecordingEngine,
    _fake_adapters,
    apply_flag,
    apply_life_temperature,
)
from daengs_evals.answer_quality.provenance import source_provenance, utc_now
from daengs_evals.answer_quality.questions import file_sha256
from daengs_evals.profile_fitness.profiles import (
    ASSETS_DIR,
    PROFILES_V1_PATH,
    Profile,
    load_profiles,
    profiles_by_id,
    require_dog_wiring,
)
from daengs_evals.profile_fitness.questions import (
    ABSENT_ARM,
    BASELINE,
    QUESTIONS_V1_PATH,
    Question,
    check_against_profiles,
    load_questions,
)

_PRINCIPAL = PrincipalContext(subject="profile-fitness-runner", kind="ADMIN")
CONDITIONS: tuple[str, ...] = ("contrast", "ablation", "noise")
#: 서울 시청. 좌표는 walk · place 가 CLARIFY 를 내지 않게 하려는 것뿐이고, general 에는 안 간다.
DEFAULT_LOCATION = {"lat": 37.5665, "lon": 126.978}

CellKey = tuple[str, str, int]


def cells_path(label: str) -> Path:
    return ASSETS_DIR / f"cells_{label}.jsonl"


# ---------------------------------------------------------------------------
# 어떤 셀을 만들 것인가 — 순수 함수
# ---------------------------------------------------------------------------


def plan_cells(
    questions: Iterable[Question], conditions: Sequence[str]
) -> list[tuple[Question, str, int]]:
    """조건 → 셀 목록. 같은 셀이 두 조건에서 나오면 한 번만 만든다."""
    wanted: dict[CellKey, tuple[Question, str, int]] = {}
    for q in questions:
        if "contrast" in conditions:
            for arm in q.arms:
                wanted.setdefault((q.question_id, arm, 0), (q, arm, 0))
        if "ablation" in conditions and q.kind == "reactive":
            wanted.setdefault((q.question_id, ABSENT_ARM, 0), (q, ABSENT_ARM, 0))
            # 절제 쌍의 다른 쪽 — contrast 를 같이 안 돌려도 짝이 있게
            wanted.setdefault((q.question_id, q.arms[BASELINE], 0), (q, q.arms[BASELINE], 0))
        if "noise" in conditions:
            wanted.setdefault((q.question_id, q.arms[BASELINE], 0), (q, q.arms[BASELINE], 0))
            wanted.setdefault((q.question_id, q.arms[BASELINE], 1), (q, q.arms[BASELINE], 1))
    return list(wanted.values())


def cell_key(row: Mapping[str, Any]) -> CellKey:
    return (str(row["question_id"]), str(row["arm"]), int(row["run"]))


def selected_capabilities(plan: Mapping[str, Any] | None) -> list[str]:
    if not plan:
        return []
    names = [str(r.get("capability")) for r in plan.get("requests", [])]
    names += [f"handoff:{h.get('target')}" for h in plan.get("handoffs", [])]
    return names


# ---------------------------------------------------------------------------
# 파일
# ---------------------------------------------------------------------------


def load_cells(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    meta: dict[str, Any] | None = None
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("kind") == "meta":
            meta = row
        elif row.get("kind") == "cell":
            rows.append(row)
    if meta is None:
        raise ValueError(f"{path}: meta 행이 없다")
    return meta, rows


def _append(path: Path, row: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# 어댑터 · 오케스트레이터 조립
# ---------------------------------------------------------------------------


def _metered_general_generate(
    ledger: Meter,
) -> Callable[[str], Awaitable[object]]:
    """general 어댑터의 Gemini 호출을 **같은 설정으로** 하되 사용량을 센다.

    `_generate_with_gemini` 는 응답 객체를 버려 `usage_metadata` 가 남지 않는다. 생성 설정은
    운영의 `general_generation_config()` **그 객체**를 쓴다 — 숫자를 다시 적으면 운영과
    평가가 조용히 갈린다.
    """
    from daengs_backend.orchestration.adapters.general import (
        GENERAL_MODEL_ID,
        general_generation_config,
    )
    from daengs_backend.orchestration.semantic import _gemini_client

    async def generate(prompt: str) -> object:
        def _call() -> object:
            response = _gemini_client().models.generate_content(
                model=GENERAL_MODEL_ID, contents=prompt, config=general_generation_config()
            )
            usage = getattr(response, "usage_metadata", None)
            ledger.add(
                input_tokens=getattr(usage, "prompt_token_count", None),
                output_tokens=int(getattr(usage, "candidates_token_count", None) or 0)
                + int(getattr(usage, "thoughts_token_count", None) or 0),
            )
            parsed = getattr(response, "parsed", None)
            return parsed if parsed is not None else getattr(response, "text", None)

        return await asyncio.to_thread(_call)

    return generate


def build_adapters(mode: str, general_meter: Meter) -> Mapping[CapabilityName, Any] | None:
    if mode == "real":
        return None
    if mode == "fake":
        return _fake_adapters()
    if mode == "fallback-only":
        from daengs_backend.orchestration.adapters.general import GeneralCapabilityAdapter

        adapters: dict[CapabilityName, Any] = dict(_fake_adapters())
        adapters[CapabilityName.GENERAL] = GeneralCapabilityAdapter(
            generate=_metered_general_generate(general_meter)
        )
        return adapters
    raise ValueError(f"--adapters 는 real|fake|fallback-only 입니다: {mode!r}")


def build_orchestrator(
    mode: str, router_meter: Meter, general_meter: Meter, sink: dict[str, Any]
) -> Any:
    from daengs_backend.orchestration.semantic import GeminiSemanticRouter
    from daengs_backend.orchestration.service import AssistantOrchestrationService
    from daengs_evals.orchestrator_comparison.runner import _metered_semantic_generate

    return AssistantOrchestrationService(
        engine=RecordingEngine(build_adapters(mode, general_meter), sink),  # type: ignore[arg-type]
        semantic_router=GeminiSemanticRouter(generate=_metered_semantic_generate(router_meter)),
    )


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------


async def run_cell(
    orchestrator: Any,
    question: Question,
    profile: Profile,
    run: int,
    *,
    router_meter: Meter,
    general_meter: Meter,
    sink: dict[str, Any],
    location: Mapping[str, float] | None = DEFAULT_LOCATION,
) -> dict[str, Any]:
    router_meter.reset()
    general_meter.reset()
    sink["plan"] = None
    started = time.perf_counter()
    row: dict[str, Any] = {
        "kind": "cell",
        "question_id": question.question_id,
        "arm": profile.profile_id,
        "run": run,
        "status": "RUNNER_ERROR",
        "message": "",
        "results": [],
        "handoffs": [],
        "clarify": None,
        "plan": None,
        "capabilities": [],
        "error": None,
    }
    base = {"location": dict(location)} if location else {}
    try:
        response = await orchestrator.run(
            query=question.query, principal=_PRINCIPAL, context=profile.context(base)
        )
        row.update(
            status=response.status.value,
            message=response.message,
            results=[r.model_dump(mode="json") for r in response.results],
            handoffs=[h.model_dump(mode="json") for h in response.handoffs],
            clarify=response.clarify.model_dump(mode="json") if response.clarify else None,
        )
    except Exception as exc:  # noqa: BLE001 - 한 셀의 실패가 수집을 멈추지 않는다
        row["error"] = f"{type(exc).__name__}: {exc}"
    plan = sink["plan"]
    row["plan"] = plan.model_dump(mode="json") if plan is not None else None
    row["capabilities"] = selected_capabilities(row["plan"])
    row["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
    row["router_tokens"] = {
        "input": router_meter.input_tokens,
        "output": router_meter.output_tokens,
    }
    row["general_tokens"] = {
        "input": general_meter.input_tokens,
        "output": general_meter.output_tokens,
    }
    row["collected_at"] = utc_now()
    return row


def build_meta(
    *,
    label: str,
    conditions: Sequence[str],
    adapters: str,
    flag: Mapping[str, Any],
    life_temperature: Mapping[str, Any],
    questions_path: Path,
    profiles_path: Path,
) -> dict[str, Any]:
    from daengs_backend.orchestration.adapters.general import (
        GENERAL_MODEL_ID,
        GENERAL_PROMPT_VERSION,
    )
    from daengs_backend.orchestration.semantic import PROMPT_VERSION, ROUTER_MODEL_ID

    return {
        "label": label,
        "conditions": list(conditions),
        "adapters": adapters,
        "general_fallback": dict(flag),
        "life_temperature": dict(life_temperature),
        "router": {"model": ROUTER_MODEL_ID, "prompt_version": PROMPT_VERSION},
        "general": {"model": GENERAL_MODEL_ID, "prompt_version": GENERAL_PROMPT_VERSION},
        "questions_path": str(questions_path),
        "questions_sha256": file_sha256(questions_path),
        "profiles_path": str(profiles_path),
        "profiles_sha256": file_sha256(profiles_path),
        "location": DEFAULT_LOCATION,
        "principal": f"{_PRINCIPAL.kind} {_PRINCIPAL.subject}",
        "wiring": require_dog_wiring(),
        "source": source_provenance(),
        "started_at": utc_now(),
    }


#: 다시 돌려볼 만한 실패 — 프로바이더가 잠깐 죽었거나 시간이 초과된 것. 거절 · 기권 · 라우팅
#: 미선택은 **결과**라 다시 돌리지 않는다.
TRANSIENT_ERROR_KINDS: frozenset[str] = frozenset(
    {"general_provider_failure", "general_timeout", "general_invalid_output"}
)


def is_transient_failure(row: Mapping[str, Any]) -> bool:
    if row.get("error"):
        return True  # 러너 예외 — 네트워크 · 타임아웃 류
    # 의미 라우터가 두 번 다 스키마를 못 지켜 FAILED (plan 없음). 실측 2026-09-09 에 158셀 중 35 —
    # 질문의 성질이 아니라(같은 질문이 다른 셀에선 통과) 프로바이더 쪽 흔들림이다.
    if row.get("status") == "FAILED" and not row.get("plan") and not row.get("results"):
        return True
    for result in row.get("results") or []:
        err = result.get("error") or {}
        if (
            result.get("status") in ("ERROR", "TIMEOUT")
            and err.get("kind") in TRANSIENT_ERROR_KINDS
        ):
            return True
    return False


def _resume_or_start(
    path: Path, meta: Mapping[str, Any], *, resume: bool, retry_failed: bool = False
) -> set[CellKey]:
    """이미 있는 셀 키. 입력 해시가 다르면 이어 붙이기를 거부한다.

    `retry_failed` 면 일시적 실패 셀을 파일에서 **지우고** 다시 돌린다 — 같은 키가 두 번 남으면
    `index_cells` 가 거부하기 때문이다. 지운 수는 로그에 남긴다.
    """
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        _append(path, {"kind": "meta", **meta})
        return set()
    if not resume:
        raise FileExistsError(
            f"{path} 가 이미 있습니다 — 이어 붙이려면 --resume, 새로 하려면 다른 --label"
        )
    old_meta, rows = load_cells(path)
    for key in ("questions_sha256", "profiles_sha256", "adapters"):
        if old_meta.get(key) != meta.get(key):
            raise RuntimeError(
                f"{path}: {key} 가 다릅니다 — 다른 입력으로 만든 파일에 이어 붙이지 않습니다"
            )
    if retry_failed:
        keep = [r for r in rows if not is_transient_failure(r)]
        dropped = len(rows) - len(keep)
        if dropped:
            with path.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps({"kind": "meta", **old_meta}, ensure_ascii=False) + "\n")
                for r in keep:
                    handle.write(json.dumps(r, ensure_ascii=False) + "\n")
            print(f"  일시적 실패 셀 {dropped}개를 지우고 다시 돌립니다")
        rows = keep
    return {cell_key(r) for r in rows}


async def collect(
    *,
    label: str,
    conditions: Sequence[str],
    adapters: str,
    flag: str,
    life_temperature: float | None,
    questions_path: Path,
    profiles_path: Path,
    limit: int | None,
    resume: bool,
    retry_failed: bool = False,
    auto_retry: int = 2,
    log: Callable[[str], None] = print,
) -> Path:
    from daengs_backend.config import settings

    flag_state = apply_flag(settings, flag)
    temp_state = apply_life_temperature(life_temperature)

    questions = load_questions(questions_path)
    profiles = load_profiles(profiles_path)
    problems = check_against_profiles(questions, profiles)
    if problems:
        raise RuntimeError("질문과 프로필이 안 맞습니다:\n  " + "\n  ".join(problems))
    by_id = profiles_by_id(profiles)

    if limit:
        questions = questions[:limit]
    planned = plan_cells(questions, conditions)

    path = cells_path(label)
    meta = build_meta(
        label=label,
        conditions=conditions,
        adapters=adapters,
        flag=flag_state,
        life_temperature=temp_state,
        questions_path=questions_path,
        profiles_path=profiles_path,
    )
    done = _resume_or_start(path, meta, resume=resume, retry_failed=retry_failed)
    todo = [(q, arm, run) for q, arm, run in planned if (q.question_id, arm, run) not in done]

    log(
        f"셀 {len(planned)}개 계획 · 이미 있음 {len(done)} · 이번에 {len(todo)} — "
        f"어댑터 {adapters} · 폴백 {flag_state['effective']} · Life 온도 {temp_state['effective']}"
    )
    if flag_state["effective"] != "on":
        log(
            "  ⚠ general 폴백이 꺼져 있다 — 일상 돌봄 질문이 전부 FAILED 로 나온다 (실측 2026-09-09)"
        )

    router_meter, general_meter = Meter(), Meter()
    sink: dict[str, Any] = {"plan": None}
    orchestrator = build_orchestrator(adapters, router_meter, general_meter, sink)

    totals = {"router_in": 0, "router_out": 0, "general_in": 0, "general_out": 0}
    statuses: dict[str, int] = {}
    for i, (q, arm, run) in enumerate(todo, start=1):
        row = await run_cell(
            orchestrator,
            q,
            by_id[arm],
            run,
            router_meter=router_meter,
            general_meter=general_meter,
            sink=sink,
        )
        _append(path, row)
        statuses[row["status"]] = statuses.get(row["status"], 0) + 1
        totals["router_in"] += row["router_tokens"]["input"]
        totals["router_out"] += row["router_tokens"]["output"]
        totals["general_in"] += row["general_tokens"]["input"]
        totals["general_out"] += row["general_tokens"]["output"]
        caps = ",".join(row["capabilities"]) or "-"
        log(
            f"  [{i:>3}/{len(todo)}] {q.question_id:28s} {arm:16s} run{run} "
            f"{row['status']:9s} {caps:12s} {row['latency_ms']:>7.0f}ms"
        )

    log(
        f"끝. 상태 {statuses} · 라우터 토큰 {totals['router_in']}/{totals['router_out']} · "
        f"general 토큰 {totals['general_in']}/{totals['general_out']} → {path}"
    )
    # 일시적 실패는 스스로 두 번까지 다시 돈다. 실측(2026-09-09) 158셀 중 35 가 라우터 흔들림으로
    # 죽었고 두 번 재시도로 전부 복구됐다. 사람이 --retry-failed 를 두 번 치게 두지 않는다.
    if auto_retry > 0:
        _, rows_now = load_cells(path)
        if any(is_transient_failure(r) for r in rows_now):
            log(f"  일시적 실패가 남아 있다 — 자동 재시도 (남은 횟수 {auto_retry})")
            return await collect(
                label=label,
                conditions=conditions,
                adapters=adapters,
                flag=flag,
                life_temperature=life_temperature,
                questions_path=questions_path,
                profiles_path=profiles_path,
                limit=limit,
                resume=True,
                retry_failed=True,
                auto_retry=auto_retry - 1,
                log=log,
            )
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="개체 적합성 — 질문 × 프로필 셀 수집")
    parser.add_argument("--label", required=True)
    parser.add_argument(
        "--condition",
        choices=(*CONDITIONS, "all"),
        default="all",
        help="contrast|ablation|noise|all",
    )
    parser.add_argument(
        "--adapters", choices=("real", "fake", "fallback-only"), default="fallback-only"
    )
    parser.add_argument(
        "--flag", choices=("on", "off"), default="on", help="general 폴백. off 는 실험용"
    )
    parser.add_argument("--life-temperature", type=float, default=0.0)
    parser.add_argument("--questions", default=str(QUESTIONS_V1_PATH))
    parser.add_argument("--profiles", default=str(PROFILES_V1_PATH))
    parser.add_argument("--limit", type=int, default=None, help="앞에서부터 N 문항만")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="--resume 과 함께. 일시적 실패 셀을 지우고 다시 돌린다",
    )
    args = parser.parse_args(argv)

    conditions = list(CONDITIONS) if args.condition == "all" else [args.condition]
    asyncio.run(
        collect(
            label=args.label,
            conditions=conditions,
            adapters=args.adapters,
            flag=args.flag,
            life_temperature=args.life_temperature,
            questions_path=Path(args.questions),
            profiles_path=Path(args.profiles),
            limit=args.limit,
            resume=args.resume,
            retry_failed=args.retry_failed,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
