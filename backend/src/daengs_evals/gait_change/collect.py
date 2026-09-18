"""실제 Gemini 로 셀을 모은다 — **느리게** (#575).

    uv run python -m daengs_evals.gait_change.collect --label gc_v1
    uv run python -m daengs_evals.gait_change.collect --label gc_v1 --resume
    uv run python -m daengs_evals.gait_change.collect --label gc_v1 --resume --max-calls 30 --interval 10

셀 = (문항, 반복 번호). 운영 어댑터 `GaitCapabilityAdapter` 를 **그대로** 부르고, 모델 호출만
`generate=` 로 감싸서 간격 · 재시도 · 원출력 기록을 붙인다 — 운영 코드는 한 줄도 안 바뀐다
(`skin_guidance.collect` 와 같은 방법). 생성 설정도 운영의 `gait_generation_config()` 그 객체다.
숫자를 다시 적으면 운영과 평가가 조용히 갈린다.

**무료 키 전제.** 기본은 호출 사이 6초(분당 10회 이하), 429(분당 한도) · 503(수요 폭주)은
15 → 30 → 60초 쉬고 다시, 실행 한 번에 호출 60회까지. 하루 한도(`PerDay`)가 찼다는 429 가 오면
**그 셀은 적지 않고 즉시 멈춘다** — 다음 날 `--resume` 이 거기서 잇는다. 상한도 호출 시도
기준이라 재시도까지 센다.

**240콜이라 하루에 안 끝난다.** 4갈래 × 20문항 × 3회다. 무료 키는 200콜 안팎에서 막히므로
같은 `--label` 로 이틀에 나눠 받는 것이 정상 경로다.

**재개된다.** 파일에 이미 있는 셀은 건너뛴다. 프로바이더 실패로 끝난 셀(`transient`)은 다시
돈다. meta 의 문항 해시 · 프롬프트 버전 · 모델 · 반복 수가 다르면 이어 붙이기를 거부한다 —
다른 입력으로 만든 셀이 한 파일에 섞이면 분모가 틀린다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from daengs_backend.orchestration.adapters.gait import (
    GAIT_MODEL_ID,
    GAIT_PROMPT_VERSION,
    GaitCapabilityAdapter,
)
from daengs_backend.orchestration.contracts import CapabilityName, CapabilityRequest
from daengs_evals.answer_quality.provenance import utc_now
from daengs_evals.gait_change.questions import (
    ASSETS_DIR,
    QUESTIONS_V1_PATH,
    Question,
    compare_context,
    conversation_context,
    file_sha256,
    load_questions,
)

DEFAULT_INTERVAL_S = 6.0
DEFAULT_MAX_CALLS = 60
DEFAULT_REPEATS = 3
BACKOFF_S = (15.0, 30.0, 60.0)
#: 이 오류로 끝난 셀은 모델의 답이 아니라 프로바이더 사정이라 `--resume` 에서 다시 돈다.
TRANSIENT_ERROR_KINDS = frozenset({"gait_provider_failure", "gait_timeout"})

#: (프롬프트) → (원출력, 사용량). 테스트가 가짜를 꽂는다.
ModelCall = Callable[[str], tuple[object, dict[str, Any]]]


class CallBudgetExhausted(RuntimeError):
    """실행당 호출 상한에 닿았다. 어댑터는 이것을 ERROR 로 바꾸지만, 수집기는 셀을 적지 않고 멈춘다."""


def classify_provider_error(exc: BaseException) -> str:
    """`daily_quota`(오늘은 그만) · `retry`(쉬고 다시) · `fatal`(그대로 올림)."""
    code = getattr(exc, "code", None)
    text = str(exc)
    if code == 429 or "RESOURCE_EXHAUSTED" in text or text.startswith("429"):
        return "daily_quota" if ("PerDay" in text or "per day" in text.lower()) else "retry"
    if code in (500, 502, 503, 504) or "UNAVAILABLE" in text or text.startswith("503"):
        return "retry"
    return "fatal"


@dataclass
class Pacer:
    """실행 전체가 나눠 쓰는 속도 · 상한 상태. 셀이 바뀌어도 간격은 이어서 잰다."""

    interval: float
    max_calls: int
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    backoff: tuple[float, ...] = BACKOFF_S
    used: int = 0
    last_at: float | None = None
    quota_exhausted: bool = False
    budget_exhausted: bool = False
    log: Callable[[str], None] = field(default=lambda _msg: None, repr=False)

    def acquire(self) -> None:
        if self.used >= self.max_calls:
            self.budget_exhausted = True
            raise CallBudgetExhausted(f"실행당 호출 상한 {self.max_calls}회")
        if self.last_at is not None:
            remaining = self.interval - (self.clock() - self.last_at)
            if remaining > 0:
                self.sleep(remaining)
        self.used += 1
        self.last_at = self.clock()


class CellGenerate:
    """셀 하나의 모델 호출. 어댑터의 `generate=` 자리에 들어간다."""

    def __init__(self, pacer: Pacer, call: ModelCall) -> None:
        self._pacer = pacer
        self._call = call
        self.raw: object | None = None
        self.usage: dict[str, Any] = {}
        self.attempts = 0

    async def generate(self, prompt: str) -> object:
        return await asyncio.to_thread(self._generate, prompt)

    def _generate(self, prompt: str) -> object:
        pacer = self._pacer
        for attempt in range(len(pacer.backoff) + 1):
            pacer.acquire()
            self.attempts += 1
            try:
                raw, usage = self._call(prompt)
            except Exception as exc:
                kind = classify_provider_error(exc)
                if kind == "daily_quota":
                    pacer.quota_exhausted = True
                    raise
                if kind == "retry" and attempt < len(pacer.backoff):
                    wait = pacer.backoff[attempt]
                    pacer.log(
                        f"    · 프로바이더 {getattr(exc, 'code', '?')} — {wait:.0f}초 쉬고 다시"
                    )
                    pacer.sleep(wait)
                    continue
                raise
            self.raw = raw
            self.usage = usage
            return raw
        raise RuntimeError("unreachable")  # pragma: no cover


def _call_gemini(prompt: str) -> tuple[object, dict[str, Any]]:
    """운영 어댑터의 `_generate_with_gemini` 와 같은 호출에 사용량만 더 받는다."""
    from daengs_backend.orchestration.adapters.gait import gait_generation_config
    from daengs_backend.orchestration.semantic import _gemini_client

    response = _gemini_client().models.generate_content(
        model=GAIT_MODEL_ID, contents=prompt, config=gait_generation_config()
    )
    usage = getattr(response, "usage_metadata", None)
    parsed = getattr(response, "parsed", None)
    raw = parsed if parsed is not None else getattr(response, "text", None)
    return raw, {
        "input_tokens": getattr(usage, "prompt_token_count", None),
        "output_tokens": getattr(usage, "candidates_token_count", None),
    }


def _jsonable(raw: object) -> object:
    if raw is None:
        return None
    if hasattr(raw, "model_dump"):
        return raw.model_dump(mode="json")  # type: ignore[union-attr]
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"unparsed": raw}
    if isinstance(raw, Mapping):
        return dict(raw)
    return {"unparsed": repr(raw)}


# ---------------------------------------------------------------------------
# 파일
# ---------------------------------------------------------------------------


def cells_path(label: str) -> Path:
    return ASSETS_DIR / f"cells_{label}.jsonl"


def cell_id(question: Question, run: int) -> str:
    return f"{question.question_id}#{run}"


def load_cells(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    meta: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if "meta" in row:
            meta = row["meta"]
        else:
            rows.append(row)
    return meta, rows


def _append(path: Path, row: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def build_meta(label: str, questions_path: Path, repeats: int) -> dict[str, Any]:
    try:
        shown_path = questions_path.resolve().relative_to(ASSETS_DIR.parents[1]).as_posix()
    except ValueError:
        shown_path = questions_path.as_posix()
    return {
        "label": label,
        "prompt_version": GAIT_PROMPT_VERSION,
        "model": GAIT_MODEL_ID,
        "questions_path": shown_path,
        "questions_sha256": file_sha256(questions_path),
        "repeats": repeats,
        "created_at": utc_now(),
    }


_MUST_MATCH = ("prompt_version", "model", "questions_sha256", "repeats")


def _start_or_resume(path: Path, meta: Mapping[str, Any], resume: bool) -> set[str]:
    if path.exists():
        if not resume:
            raise SystemExit(
                f"{path} 가 이미 있습니다 — 이어 하려면 --resume, 새로 하려면 다른 --label"
            )
        existing, rows = load_cells(path)
        for key in _MUST_MATCH:
            if existing.get(key) != meta[key]:
                raise SystemExit(
                    f"{path} 의 {key} 가 다릅니다 ({existing.get(key)!r} ≠ {meta[key]!r}) — 다른 --label 로"
                )
        return {row["cell_id"] for row in rows if not row.get("transient")}
    path.parent.mkdir(parents=True, exist_ok=True)
    _append(path, {"meta": dict(meta)})
    return set()


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------


def _request(question: Question) -> CapabilityRequest:
    payload: dict[str, Any] = {
        "question": question.query,
        "compare": compare_context(question),
    }
    # 앞 대화 (D-082). **없으면 칸 자체를 안 넣는다** — 운영도 그렇게 조립한다.
    conversation = conversation_context(question)
    if conversation is not None:
        payload["conversation"] = conversation
    return CapabilityRequest(capability=CapabilityName.GAIT, payload=payload)


def _row(question: Question, run: int, result: Any, gen: CellGenerate) -> dict[str, Any]:
    data = result.data or {}
    error_kind = result.error.kind if result.error else None
    status = result.status.value
    return {
        "cell_id": cell_id(question, run),
        "question_id": question.question_id,
        "change_kind": question.change_kind,
        "category": question.category,
        "scenario": question.scenario,
        #: 이 셀이 앞 대화를 실었나 (D-082). 리포트가 이 값으로 갈라 센다.
        "has_conversation": question.has_conversation,
        "run": run,
        "status": status,
        "refusal_code": result.refusal.code if result.refusal else None,
        "answer": data.get("answer"),
        "actions": data.get("actions"),
        "guarded": data.get("guarded"),
        "expert_advisory": data.get("expert_advisory"),
        #: 대조를 통과해 실제로 문장이 된 병명. 모델이 지어낸 것은 여기 안 온다 —
        #: 원출력과 비교하면 어댑터가 버린 건이 보인다 (`raw.owner_condition`).
        "owner_condition": data.get("owner_condition"),
        "error_kind": error_kind,
        "transient": status in ("ERROR", "TIMEOUT") and error_kind in TRANSIENT_ERROR_KINDS,
        "raw": _jsonable(gen.raw),
        "attempts": gen.attempts,
        "usage": gen.usage,
        "elapsed_ms": result.elapsed_ms,
        "at": utc_now(),
    }


async def collect(
    *,
    label: str,
    questions_path: Path = QUESTIONS_V1_PATH,
    repeats: int = DEFAULT_REPEATS,
    out_path: Path | None = None,
    max_calls: int = DEFAULT_MAX_CALLS,
    interval: float = DEFAULT_INTERVAL_S,
    resume: bool = False,
    limit: int | None = None,
    call: ModelCall | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    questions = load_questions(questions_path)
    path = out_path or cells_path(label)
    meta = build_meta(label, questions_path, repeats)
    done = _start_or_resume(path, meta, resume)

    planned = [(q, run) for q in questions for run in range(repeats)]
    todo = [(q, run) for q, run in planned if cell_id(q, run) not in done]
    if limit is not None:
        todo = todo[:limit]
    pacer = Pacer(interval=interval, max_calls=max_calls, sleep=sleep, clock=clock, log=log)
    model_call = call or _call_gemini

    recorded = 0
    stopped: str | None = None
    for question, run in todo:
        gen = CellGenerate(pacer, model_call)
        adapter = GaitCapabilityAdapter(generate=gen.generate)
        result = await adapter.run(_request(question), request_id=cell_id(question, run))
        if pacer.quota_exhausted:
            stopped = "daily_quota"
            log("하루 한도가 찼습니다 — 이 셀은 적지 않았습니다. 내일 --resume 으로 이어 주세요.")
            break
        if pacer.budget_exhausted:
            stopped = "max_calls"
            break
        row = _row(question, run, result, gen)
        _append(path, row)
        recorded += 1
        mark = "재시도 대상" if row["transient"] else (row["refusal_code"] or row["actions"])
        log(
            f"[{len(done) + recorded}/{len(planned)}] {row['cell_id']} {row['status']} {mark}"
            f"  (호출 {pacer.used}/{max_calls})"
        )

    _, rows = load_cells(path)
    finished = {row["cell_id"] for row in rows if not row.get("transient")}
    summary = {
        "path": str(path),
        "recorded": recorded,
        "stopped": stopped,
        "calls_used": pacer.used,
        "finished": len(finished),
        "planned": len(planned),
    }
    log(
        f"이번 실행: {recorded}셀 기록 · 호출 {pacer.used}회 · 전체 {len(finished)}/{len(planned)} 완료"
        + (f" · 멈춘 이유 {stopped}" if stopped else "")
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--label", required=True)
    parser.add_argument("--questions", default=str(QUESTIONS_V1_PATH))
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S)
    parser.add_argument("--limit", type=int, default=None, help="이번 실행에서 돌 셀 수")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    summary = asyncio.run(
        collect(
            label=args.label,
            questions_path=Path(args.questions),
            repeats=args.repeats,
            max_calls=args.max_calls,
            interval=args.interval,
            resume=args.resume,
            limit=args.limit,
        )
    )
    return 0 if summary["stopped"] != "daily_quota" else 3


if __name__ == "__main__":
    raise SystemExit(main())
