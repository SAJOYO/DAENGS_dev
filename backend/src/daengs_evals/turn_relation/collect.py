"""대본을 오케스트레이터에 재생하고 리졸버가 낸 관계를 받아 적는다.

    uv run python -m daengs_evals.turn_relation.collect --label tr_v1
    uv run python -m daengs_evals.turn_relation.collect --label tr_v1 --resume

셀 파일: `evals/turn_relation/runs_<label>.jsonl`. 첫 줄 meta, 이후 대본당 한 줄. 재개하면 이미 있는
대본은 건너뛴다.

리졸버는 **감싸서** 본다 (`RecordingResolver`). `service._plan_and_execute` 는 NEW 이거나 확신이 낮으면
결과를 버리고 라우터로 가는데, 그 버리기 전의 원값이 우리가 재려는 것이다 — 응답만 봐서는 "관계를
못 맞혔다" 와 "맞혔는데 확신이 낮아 버렸다" 가 안 갈린다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from daengs_backend.orchestration.contracts import CapabilityName, ObservationAxis, PrincipalContext
from daengs_evals.answer_quality.collect import Meter, RecordingEngine, _fake_adapters, apply_flag
from daengs_evals.answer_quality.provenance import source_provenance, utc_now
from daengs_evals.answer_quality.questions import file_sha256
from daengs_evals.profile_fitness.profiles import ASSETS_DIR as _PF_ASSETS

ASSETS_DIR = _PF_ASSETS.parent / "turn_relation"
SCRIPTS_V1_PATH = ASSETS_DIR / "scripts_v1.jsonl"

#: 대본의 기대 관계. `META` 는 리졸버 프롬프트에는 있는데 `TurnRelation` 열거형에는 없다 — 그래서 여기서는
#: 문자열로 두고, 리졸버가 그걸 어떻게 처리하는지(스키마 실패? 다른 값으로 접힘?)를 결과로 본다.
Expect = Literal["NEW", "FOLLOW_UP", "CORRECTION", "REPEAT", "META"]
STRATA: tuple[str, ...] = (
    "new_unrelated",
    "followup_marker",
    "followup_no_marker",
    "correction",
    "repeat",
    "meta",
    "pending_answer",
)
_PRINCIPAL = PrincipalContext(subject="turn-relation-runner", kind="ADMIN")
#: 되묻기에 답할 때 강아지가 있어야 자연스럽다. general 은 이 프로필을 보지만 관계 판정은 안 본다.
_DOG = {"breed": "말티즈", "age_months": 48}


class PriorPair(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user: str = Field(min_length=1)
    assistant: str = Field(min_length=1)


class Pending(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1)
    missing_axes: list[ObservationAxis] = Field(default_factory=list, max_length=2)


class Script(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script_id: str = Field(pattern=r"^tr_[a-z]+_\d{2}$")
    stratum: str
    prior: list[PriorPair] = Field(min_length=1, max_length=3)
    pending: Pending | None = None
    current: str = Field(min_length=1)
    expect: Expect
    author: str
    note: str | None = None


def load_scripts(path: Path | str = SCRIPTS_V1_PATH) -> list[Script]:
    out: list[Script] = []
    seen: set[str] = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        s = Script.model_validate(json.loads(line))
        if s.stratum not in STRATA:
            raise ValueError(f"{s.script_id}: 모르는 층 {s.stratum!r}")
        if s.script_id in seen:
            raise ValueError(f"대본 id 중복: {s.script_id}")
        seen.add(s.script_id)
        out.append(s)
    return out


# ---------------------------------------------------------------------------
# 리졸버 기록기
# ---------------------------------------------------------------------------


def _metered_resolver_generate(meter: Meter, sink: dict[str, Any]):
    """`resolver._generate_with_gemini` 와 같은 호출인데 usage 를 남긴다 — 라우터 쪽 `_metered_semantic_generate`
    와 같은 이유(그쪽은 응답 객체를 버려 토큰이 안 남는다)."""
    from daengs_backend.orchestration.resolver import (
        TURN_RESOLVER_MODEL_ID,
        _gemini_client,
        _turn_resolver_generation_config,
    )

    async def generate(prompt: str) -> object:
        def _call() -> object:
            sink["generate_called"] = True  # 빠른 길(표지어 없음)은 여기까지 안 온다
            response = _gemini_client().models.generate_content(
                model=TURN_RESOLVER_MODEL_ID,
                contents=prompt,
                config=_turn_resolver_generation_config(),
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


class RecordingResolver:
    """진짜 리졸버를 감싸 원값을 `sink` 에 남긴다. 예외도 남기고 그대로 올린다 — 서비스가 하던 대로."""

    def __init__(self, inner: Any, sink: dict[str, Any]) -> None:
        self._inner = inner
        self._sink = sink

    async def resolve(self, *, query: str, candidates: Sequence[Any], pending: Any) -> Any:
        self._sink["resolve_called"] = True
        try:
            resolved = await self._inner.resolve(
                query=query, candidates=candidates, pending=pending
            )
        except Exception as exc:
            self._sink["error"] = {"kind": type(exc).__name__, "detail": str(exc)[:300]}
            raise
        self._sink["resolved"] = resolved
        return resolved


def build_orchestrator(
    router_meter: Meter, general_meter: Meter, resolver_meter: Meter, sink: dict[str, Any]
) -> Any:
    from daengs_backend.orchestration.adapters.general import GeneralCapabilityAdapter
    from daengs_backend.orchestration.resolver import GeminiTurnResolver
    from daengs_backend.orchestration.semantic import GeminiSemanticRouter
    from daengs_backend.orchestration.service import AssistantOrchestrationService
    from daengs_evals.eval_harness import metered_semantic_generate as _metered_semantic_generate
    from daengs_evals.profile_fitness.collect import _metered_general_generate

    adapters: dict[CapabilityName, Any] = dict(_fake_adapters())
    adapters[CapabilityName.GENERAL] = GeneralCapabilityAdapter(
        generate=_metered_general_generate(general_meter)
    )
    return AssistantOrchestrationService(
        engine=RecordingEngine(adapters, sink),  # type: ignore[arg-type]
        semantic_router=GeminiSemanticRouter(generate=_metered_semantic_generate(router_meter)),
        turn_resolver=RecordingResolver(
            GeminiTurnResolver(generate=_metered_resolver_generate(resolver_meter, sink)), sink
        ),
    )


# ---------------------------------------------------------------------------
# 대본 하나
# ---------------------------------------------------------------------------


def prior_turns_of(script: Script) -> tuple[list[Any], Any | None]:
    """대본 → 서비스가 받는 `PriorTurn` 목록 + `PendingClarification`. turn_id 는 대본 안에서만 뜻이 있다."""
    from daengs_backend.orchestration.resolver import PendingClarification, PriorTurn

    turns = [
        PriorTurn(
            turn_id=uuid.uuid5(uuid.NAMESPACE_URL, f"{script.script_id}/{i}"),
            user=p.user,
            assistant=p.assistant,
        )
        for i, p in enumerate(script.prior)
    ]
    pending = None
    if script.pending is not None:
        # 되묻기는 가장 최근 완료 턴에 붙는다 — 그 턴의 id 를 쓴다
        pending = PendingClarification(
            turn_id=turns[-1].turn_id,
            question=script.pending.question,
            missing=["observation"],
            missing_axes=list(script.pending.missing_axes),
        )
    return turns, pending


def _observed(sink: dict[str, Any], turns: Sequence[Any]) -> dict[str, Any]:
    """리졸버 원값을 파일에 적을 모양으로. `referenced` 는 앞 턴의 **번호**(0 부터)다."""
    index = {t.turn_id: i for i, t in enumerate(turns)}
    resolved = sink.get("resolved")
    if resolved is None:
        return {
            "model_called": bool(sink.get("generate_called")),
            "error": sink.get("error"),
            "relation": None,
            "confidence": None,
        }
    return {
        "model_called": bool(sink.get("generate_called")),
        "error": None,
        "relation": str(resolved.relation),
        "confidence": resolved.resolution_confidence,
        "referenced": index.get(resolved.referenced_turn_id),
        "pending_used": resolved.pending_clarification_id is not None,
        "standalone_query": resolved.standalone_query,
        "ambiguity": resolved.ambiguity,
        "context_used": len(resolved.context_used),
    }


async def run_script(
    script: Script,
    *,
    orchestrator: Any,
    router_meter: Meter,
    general_meter: Meter,
    resolver_meter: Meter,
    sink: dict[str, Any],
) -> dict[str, Any]:
    for m in (router_meter, general_meter, resolver_meter):
        m.reset()
    sink.clear()
    turns, pending = prior_turns_of(script)
    started = time.perf_counter()
    row: dict[str, Any] = {
        "kind": "run",
        "script_id": script.script_id,
        "stratum": script.stratum,
        "expect": script.expect,
        "current": script.current,
    }
    try:
        response = await orchestrator.run(
            query=script.current,
            principal=_PRINCIPAL,
            context={"dog": dict(_DOG)},
            include_route_trace=True,
            prior_turns=turns,
            pending_clarification=pending,
        )
    except Exception as exc:  # noqa: BLE001 - 러너 실패는 결과가 아니다, 따로 적는다
        row.update(
            observed=_observed(sink, turns), runner_error=f"{type(exc).__name__}: {exc}"[:300]
        )
    else:
        row.update(
            observed=_observed(sink, turns),
            status=response.status.value,
            capabilities=[r.capability.value for r in response.results],
            message=response.message,
            clarify=response.clarify.model_dump(mode="json") if response.clarify else None,
            router_prompt_version=response.route.prompt_version if response.route else None,
        )
    row["latency_ms"] = int((time.perf_counter() - started) * 1000)
    row["tokens"] = {
        "resolver": {"in": resolver_meter.input_tokens, "out": resolver_meter.output_tokens},
        "router": {"in": router_meter.input_tokens, "out": router_meter.output_tokens},
        "general": {"in": general_meter.input_tokens, "out": general_meter.output_tokens},
    }
    return row


# ---------------------------------------------------------------------------
# 파일
# ---------------------------------------------------------------------------


def runs_path(label: str) -> Path:
    return ASSETS_DIR / f"runs_{label}.jsonl"


def load_runs(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    meta: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            (rows.append(r) if r.get("kind") != "meta" else meta.update(r))
    return meta, rows


def build_meta(*, label: str, scripts_path: Path, flag: str) -> dict[str, Any]:
    from daengs_backend.orchestration.resolver import (
        RESOLUTION_CONFIDENCE_FLOOR,
        TURN_RESOLVER_MODEL_ID,
        TURN_RESOLVER_PROMPT_VERSION,
    )
    from daengs_backend.orchestration.semantic import PROMPT_VERSION, ROUTER_MODEL_ID

    return {
        "kind": "meta",
        "label": label,
        "scripts": str(scripts_path),
        "scripts_sha256": file_sha256(scripts_path),
        "resolver": {
            "model": TURN_RESOLVER_MODEL_ID,
            "prompt_version": TURN_RESOLVER_PROMPT_VERSION,
            "confidence_floor": RESOLUTION_CONFIDENCE_FLOOR,
        },
        "router": {"model": ROUTER_MODEL_ID, "prompt_version": PROMPT_VERSION},
        "adapters": "fallback-only",
        "general_fallback": flag,
        "started_at": utc_now(),
        "source": source_provenance(),
    }


def is_transient(row: dict[str, Any]) -> bool:
    """다시 돌려도 되는 실패 — 제공자 한도(429)·라우터 실패. 관계를 못 맞힌 것은 결과라 안 건드린다."""
    if row.get("runner_error"):
        return True
    err = (row.get("observed") or {}).get("error") or {}
    if "provider failed" in str(err.get("detail", "")):
        return True
    return row.get("status") == "FAILED"


async def collect(
    *,
    label: str,
    scripts_path: Path,
    flag: str,
    resume: bool,
    limit: int | None,
    retry_failed: bool = False,
    pace: float = 0.0,
) -> Path:
    from daengs_backend.config import settings

    apply_flag(settings, flag)
    if not settings.turn_resolver:
        raise RuntimeError(
            "settings.turn_resolver 가 꺼져 있다 — 이 시험은 리졸버가 도는 상태를 잰다"
        )
    scripts = load_scripts(scripts_path)
    if limit:
        scripts = scripts[:limit]
    out = runs_path(label)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    if resume and out.exists():
        meta, rows = load_runs(out)
        if retry_failed:
            # 일시적 실패 줄을 지우고 파일을 다시 쓴다 — 관계를 틀린 줄은 결과라 남긴다
            kept = [r for r in rows if not is_transient(r)]
            print(f"  일시적 실패 {len(rows) - len(kept)}개를 지우고 다시 돌립니다")
            with out.open("w", encoding="utf-8") as h:
                h.write(json.dumps(meta, ensure_ascii=False) + "\n")
                for r in kept:
                    h.write(json.dumps(r, ensure_ascii=False) + "\n")
            rows = kept
        done = {r["script_id"] for r in rows if not r.get("runner_error")}
    else:
        out.write_text(
            json.dumps(
                build_meta(label=label, scripts_path=scripts_path, flag=flag), ensure_ascii=False
            )
            + "\n",
            encoding="utf-8",
        )
    todo = [s for s in scripts if s.script_id not in done]
    print(f"대본 {len(scripts)} · 이미 {len(done)} · 이번에 {len(todo)}")

    router_meter, general_meter, resolver_meter = Meter(), Meter(), Meter()
    sink: dict[str, Any] = {}
    orchestrator = build_orchestrator(router_meter, general_meter, resolver_meter, sink)
    total = {"resolver": [0, 0], "router": [0, 0], "general": [0, 0]}
    with out.open("a", encoding="utf-8") as h:
        for i, s in enumerate(todo, 1):
            if pace and i > 1:
                # Gemini 무료 등급은 모델당 분당 15요청. 대본 하나가 리졸버·라우터·general 세 번이라
                # 12초 이상 띄워야 한도 안이다 (2026-09-11 실측: 9번째 대본부터 429).
                await asyncio.sleep(pace)
            row = await run_script(
                s,
                orchestrator=orchestrator,
                router_meter=router_meter,
                general_meter=general_meter,
                resolver_meter=resolver_meter,
                sink=sink,
            )
            h.write(json.dumps(row, ensure_ascii=False) + "\n")
            h.flush()
            for k, acc in total.items():
                acc[0] += row["tokens"][k]["in"]
                acc[1] += row["tokens"][k]["out"]
            o = row["observed"]
            got = o.get("relation") or (o.get("error") or {}).get("kind") or "-"
            conf = f"{o['confidence']:.2f}" if o.get("confidence") is not None else "  -  "
            called = "모델" if o.get("model_called") else "빠른길"
            print(
                f"  [{i:3d}/{len(todo)}] {s.script_id:14s} 기대 {s.expect:10s} → {got:16s} {conf} {called:4s} "
                f"{row.get('status', 'ERR'):9s} {row['latency_ms']:6d}ms"
            )
    print("끝. 토큰 " + " · ".join(f"{k} {v[0]}/{v[1]}" for k, v in total.items()) + f" → {out}")
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="시험 ④ 앞 대화 기억 — 대본 재생")
    parser.add_argument("--label", required=True)
    parser.add_argument("--scripts", default=str(SCRIPTS_V1_PATH))
    parser.add_argument("--flag", choices=("on", "off"), default="on")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--retry-failed", action="store_true", help="--resume 와 함께. 429·라우터 실패 줄만 다시"
    )
    parser.add_argument(
        "--pace", type=float, default=0.0, help="대본 사이 쉬는 초. 무료 Gemini 키면 13 이상"
    )
    args = parser.parse_args(argv)
    asyncio.run(
        collect(
            label=args.label,
            scripts_path=Path(args.scripts),
            flag=args.flag,
            resume=args.resume,
            limit=args.limit,
            retry_failed=args.retry_failed,
            pace=args.pace,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
