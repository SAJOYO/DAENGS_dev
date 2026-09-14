"""되묻기가 끝나는가 — 대본을 턴마다 **진짜로** 이어 돌린다.

    uv run python -m daengs_evals.ask_loop.collect --label al_v1 --pace 13
    uv run python -m daengs_evals.ask_loop.collect --label al_v1 --resume --retry-failed --pace 13

시험 ④ 는 앞 대화를 대본에 적어 두고 마지막 한 턴만 보냈다. 이 시험은 첫 턴부터 보내고, 서버가 **실제로 낸
답**을 다음 턴의 앞 대화로 넘긴다. 2026-09-12 실사용 루프("토해 → 초록토 → 밥도 안 먹어" 에 글자까지 같은
질문이 세 번)는 이렇게 이어 돌려야만 보인다 — 팀 기록: "단위 테스트도 평가 랩도 통과했지만 실사용 흐름을
이어서 돌려 보지 않아 루프를 못 봤다" (`docs/decisions.md` D-068 후속).

앞 대화를 넘기는 모양은 서버(`services/chat.py` `candidates_of` · `pending_clarification_of`)와 같다:
완료된 턴은 전부 `PriorTurn(user, assistant=message)` 이고, **가장 최근 턴이 CLARIFY 일 때만** 그 `clarify` 가
`PendingClarification` 이 된다. DB 없이 그 두 함수가 하는 일을 그대로 한다.

판정기 없음. 되묻기는 `status=CLARIFY` 로, 같은 질문 반복과 같은 항목 재질문은 문자열·항목 비교로 센다
(`report.py`).

셀 파일: `evals/ask_loop/runs_<label>.jsonl`. 첫 줄 meta, 이후 대본당 한 줄(턴 목록 포함).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from daengs_backend.orchestration.contracts import PrincipalContext
from daengs_evals.answer_quality.collect import Meter, apply_flag
from daengs_evals.answer_quality.provenance import source_provenance, utc_now
from daengs_evals.answer_quality.questions import file_sha256
from daengs_evals.turn_relation.collect import ASSETS_DIR as _TR_ASSETS
from daengs_evals.turn_relation.collect import build_orchestrator

ASSETS_DIR = _TR_ASSETS.parent / "ask_loop"
SCRIPTS_V1_PATH = ASSETS_DIR / "scripts_v1.jsonl"

STRATA: tuple[str, ...] = (
    "accumulate",  # 증상이 턴마다 하나씩 쌓인다 — 둘이 모이면 닫아야 한다
    "two_signs_at_once",  # 첫 턴에 이미 둘 — 되묻기 없이 답해야 한다
    "answered_axes",  # 물은 항목에 보호자가 답했다 — 다시 묻지 말고 답
    "brief_answer",  # 관찰이 안 나오는 짧은 답 — 한 번 물었으면 가진 것으로 답
    "topic_switch",  # 화제를 바꿨다 — 앞 되묻기를 끌고 오면 안 된다
)
_PRINCIPAL = PrincipalContext(subject="ask-loop-runner", kind="ADMIN")
_DOG = {"breed": "말티즈", "age_months": 48}


class Script(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script_id: str = Field(pattern=r"^al_[a-z]+_\d{2}$")
    stratum: str
    #: 보호자가 차례로 보내는 말. 답은 서버가 실제로 낸 것을 쓴다.
    turns: list[str] = Field(min_length=1, max_length=4)
    #: 이 턴(1부터)부터는 되묻기가 아니어야 한다.
    must_close_by: int = Field(ge=1)
    #: 첫 턴에 되묻는 것이 맞는 대본인가 (상태 질문인데 관찰이 없다).
    first_should_ask: bool
    author: str
    note: str | None = None

    @model_validator(mode="after")
    def close_within_turns(self) -> Script:
        if self.must_close_by > len(self.turns):
            raise ValueError(f"{self.script_id}: must_close_by 가 턴 수를 넘는다")
        if self.stratum not in STRATA:
            raise ValueError(f"{self.script_id}: 모르는 층 {self.stratum!r}")
        return self


def load_scripts(path: Path | str = SCRIPTS_V1_PATH) -> list[Script]:
    out: list[Script] = []
    seen: set[str] = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        s = Script.model_validate(json.loads(line))
        if s.script_id in seen:
            raise ValueError(f"대본 id 중복: {s.script_id}")
        seen.add(s.script_id)
        out.append(s)
    return out


# ---------------------------------------------------------------------------
# 앞 대화 — 서버 `candidates_of` · `pending_clarification_of` 와 같은 규칙
# ---------------------------------------------------------------------------


def turn_id_of(script_id: str, index: int) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{script_id}/{index}")


def history_of(script_id: str, done: Sequence[dict[str, Any]]) -> tuple[list[Any], Any | None]:
    """지금까지 끝난 턴 → `PriorTurn` 목록 + (마지막이 CLARIFY 면) `PendingClarification`."""
    from daengs_backend.orchestration.contracts import ObservationAxis
    from daengs_backend.orchestration.resolver import PendingClarification, PriorTurn

    turns = [
        PriorTurn(
            turn_id=turn_id_of(script_id, i), user=t["user"], assistant=t.get("message") or ""
        )
        for i, t in enumerate(done)
    ]
    pending = None
    if done and done[-1].get("status") == "CLARIFY":
        clarify = done[-1].get("clarify") or {}
        question = clarify.get("question")
        if isinstance(question, str) and question.strip():
            axes = []
            for raw in clarify.get("missing_axes") or []:
                try:
                    axes.append(ObservationAxis(raw))
                except ValueError:
                    continue
            pending = PendingClarification(
                turn_id=turns[-1].turn_id,
                question=question,
                missing=[m for m in (clarify.get("missing") or []) if isinstance(m, str)]
                or ["observation"],
                missing_axes=axes,
            )
    return turns, pending


# ---------------------------------------------------------------------------
# 대본 하나 — 턴을 이어서
# ---------------------------------------------------------------------------


async def run_script(
    script: Script,
    *,
    orchestrator: Any,
    router_meter: Meter,
    general_meter: Meter,
    resolver_meter: Meter,
    sink: dict[str, Any],
    pace: float = 0.0,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "kind": "run",
        "script_id": script.script_id,
        "stratum": script.stratum,
        "must_close_by": script.must_close_by,
        "first_should_ask": script.first_should_ask,
        "turns": [],
    }
    done: list[dict[str, Any]] = row["turns"]
    for i, user in enumerate(script.turns):
        if pace and i > 0:
            await asyncio.sleep(pace)
        for m in (router_meter, general_meter, resolver_meter):
            m.reset()
        sink.clear()
        prior, pending = history_of(script.script_id, done)
        started = time.perf_counter()
        t: dict[str, Any] = {"user": user, "pending_in": pending is not None}
        try:
            response = await orchestrator.run(
                query=user,
                principal=_PRINCIPAL,
                context={"dog": dict(_DOG)},
                include_route_trace=True,
                prior_turns=prior,
                pending_clarification=pending,
            )
        except Exception as exc:  # noqa: BLE001 - 러너 실패는 결과가 아니다, 따로 적는다
            t["runner_error"] = f"{type(exc).__name__}: {exc}"[:300]
            t["status"] = "FAILED"
        else:
            resolved = sink.get("resolved")
            t.update(
                status=response.status.value,
                capabilities=[r.capability.value for r in response.results],
                message=response.message,
                clarify=response.clarify.model_dump(mode="json") if response.clarify else None,
                relation=str(resolved.relation) if resolved is not None else None,
                router_prompt_version=response.route.prompt_version if response.route else None,
            )
        t["latency_ms"] = int((time.perf_counter() - started) * 1000)
        t["tokens"] = {
            "resolver": {"in": resolver_meter.input_tokens, "out": resolver_meter.output_tokens},
            "router": {"in": router_meter.input_tokens, "out": router_meter.output_tokens},
            "general": {"in": general_meter.input_tokens, "out": general_meter.output_tokens},
        }
        done.append(t)
        if t["status"] == "FAILED":
            break  # 이어 갈 답이 없다 — 대본을 여기서 끊고 일시적 실패로 적는다
    return row


# ---------------------------------------------------------------------------
# 파일
# ---------------------------------------------------------------------------


def runs_path(label: str) -> Path:
    return ASSETS_DIR / f"runs_{label}.jsonl"


def load_runs(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
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
    return meta or {}, rows


def build_meta(*, label: str, scripts_path: Path, flag: str) -> dict[str, Any]:
    from daengs_backend.orchestration.adapters.general import GENERAL_PROMPT_VERSION
    from daengs_backend.orchestration.resolver import (
        TURN_RESOLVER_MODEL_ID,
        TURN_RESOLVER_PROMPT_VERSION,
    )
    from daengs_backend.orchestration.semantic import PROMPT_VERSION, ROUTER_MODEL_ID

    return {
        "kind": "meta",
        "label": label,
        "scripts": str(scripts_path),
        "scripts_sha256": file_sha256(scripts_path),
        "general": {"prompt_version": GENERAL_PROMPT_VERSION},
        "resolver": {
            "model": TURN_RESOLVER_MODEL_ID,
            "prompt_version": TURN_RESOLVER_PROMPT_VERSION,
        },
        "router": {"model": ROUTER_MODEL_ID, "prompt_version": PROMPT_VERSION},
        "adapters": "fallback-only",
        "general_fallback": flag,
        "started_at": utc_now(),
        "source": source_provenance(),
    }


def is_transient(row: dict[str, Any]) -> bool:
    """다시 돌려도 되는 실패 — 어느 턴이든 러너·제공자 실패로 끊긴 대본."""
    return any(t.get("status") == "FAILED" or t.get("runner_error") for t in row.get("turns", []))


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
    done_ids: set[str] = set()
    if resume and out.exists():
        meta, rows = load_runs(out)
        if retry_failed:
            kept = [r for r in rows if not is_transient(r)]
            print(f"  끊긴 대본 {len(rows) - len(kept)}개를 지우고 다시 돌립니다")
            with out.open("w", encoding="utf-8") as h:
                h.write(json.dumps(meta, ensure_ascii=False) + "\n")
                for r in kept:
                    h.write(json.dumps(r, ensure_ascii=False) + "\n")
            rows = kept
        done_ids = {r["script_id"] for r in rows if not is_transient(r)}
    else:
        out.write_text(
            json.dumps(
                build_meta(label=label, scripts_path=scripts_path, flag=flag), ensure_ascii=False
            )
            + "\n",
            encoding="utf-8",
        )
    todo = [s for s in scripts if s.script_id not in done_ids]
    print(f"대본 {len(scripts)} · 이미 {len(done_ids)} · 이번에 {len(todo)}")

    router_meter, general_meter, resolver_meter = Meter(), Meter(), Meter()
    sink: dict[str, Any] = {}
    orchestrator = build_orchestrator(router_meter, general_meter, resolver_meter, sink)
    with out.open("a", encoding="utf-8") as h:
        for i, s in enumerate(todo, 1):
            if pace and i > 1:
                await asyncio.sleep(pace)
            row = await run_script(
                s,
                orchestrator=orchestrator,
                router_meter=router_meter,
                general_meter=general_meter,
                resolver_meter=resolver_meter,
                sink=sink,
                pace=pace,
            )
            h.write(json.dumps(row, ensure_ascii=False) + "\n")
            h.flush()
            trail = " → ".join(t.get("status", "ERR") for t in row["turns"])
            print(f"  [{i:2d}/{len(todo)}] {s.script_id:18s} 닫아야 {s.must_close_by}턴  {trail}")
    print(f"끝 → {out}")
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="되묻기 종료 시험 — 대본을 턴마다 이어 재생")
    parser.add_argument("--label", required=True)
    parser.add_argument("--scripts", default=str(SCRIPTS_V1_PATH))
    parser.add_argument("--flag", choices=("on", "off"), default="on")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--retry-failed", action="store_true", help="--resume 와 함께. 끊긴 대본만 다시"
    )
    parser.add_argument(
        "--pace", type=float, default=0.0, help="턴 사이 쉬는 초. 무료 Gemini 키면 13 이상"
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
