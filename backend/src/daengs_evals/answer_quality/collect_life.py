"""라우터 없이 Life 어댑터를 직접 불러 답을 모은다 — Life 직접 축 (#343 · RAG-079 ①).

    uv run python -m daengs_evals.answer_quality.collect_life --questions evals/answer_quality/questions_life_v1.jsonl --label life_v1_direct
    uv run python -m daengs_evals.answer_quality.collect_life --questions ... --label smoke_direct --fake --limit 3

`collect.py` 는 `/assistant/query` 축이다 — 라우터가 Life 를 고르지 않으면 그 문항에는 Life 결과가 없다
(`life_v1` 실측 86/140). 여기는 **모든 문항을 Life 에 직접** 넣는다. 부르는 것은 HTTP 가 아니라
`LifeCapabilityAdapter.run()` — 어댑터가 in-process 로 `daengs_life.app.services.ask.ask` 를 부르고 404/422/504 를
ABSTAINED/REFUSED/TIMEOUT 으로 번역하므로, 사용자에게 보이는 것과 같은 모양의 결과가 나온다 (D-035).

결과 파일의 행 모양은 `collect.py` 와 같다 — `judge score` 와 `report_life` 가 그대로 읽는다. 최상위 `status` 는
단일 능력 계획일 때 어시스턴트가 낼 값으로 옮긴다(OK→ANSWERED · REFUSED→REFUSED · 그 밖→FAILED).
"""

from __future__ import annotations

import argparse
import asyncio
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from daengs_backend.orchestration.adapters.life import LifeCapabilityAdapter
from daengs_backend.orchestration.contracts import (
    CapabilityName,
    CapabilityRequest,
    CapabilityStatus,
    LifePayload,
)
from daengs_evals.answer_quality.collect import answers_path, write_answers
from daengs_evals.answer_quality.provenance import source_provenance, utc_now
from daengs_evals.answer_quality.questions import QuestionCase, file_sha256, load_questions

_STATUS = {CapabilityStatus.OK: "ANSWERED", CapabilityStatus.REFUSED: "REFUSED"}


def _fake_ask(question: str, **_: Any) -> Any:
    # `LifeCapabilityAdapter.run()` 이 반환값을 `upstream.answer`/`.hits`/`.cited`/`.ungrounded`
    # 로 **속성** 읽는다(adapters/life.py:289-297) — dict 를 주면 OK 경로가 AttributeError 로 죽는다.
    return SimpleNamespace(answer=f"가짜: {question}", hits=[], cited=[], ungrounded=[])


def _message(result: Any) -> str:
    if result.status == CapabilityStatus.OK:
        return str((result.data or {}).get("answer") or "")
    for detail in (result.refusal, result.abstention):
        if detail is not None:
            return str(detail.message or "")
    # ErrorDetail 은 `detail` 필드다 — OutcomeDetail 의 `message` 와 이름이 다르다
    # (`contracts.py` ErrorDetail vs OutcomeDetail). getattr(..., "message", ...) 로
    # 뭉뚱그리면 항상 "" 를 돌려주므로 여기서 따로 읽는다.
    if result.error is not None:
        return str(result.error.detail or "")
    return ""


async def run_question(adapter: LifeCapabilityAdapter, case: QuestionCase) -> dict[str, Any]:
    started = time.perf_counter()
    row: dict[str, Any] = {
        "kind": "answer", "question_id": case.question_id, "stratum": case.stratum,
        "status": "FAILED", "message": "", "results": [], "handoffs": [], "clarify": None, "plan": None, "error": None,
    }
    try:
        request = CapabilityRequest(capability=CapabilityName.LIFE, payload=LifePayload(question=case.query))
        result = await adapter.run(request, request_id=f"life-direct-{case.question_id}")
        row["results"] = [result.model_dump(mode="json")]
        row["status"] = _STATUS.get(result.status, "FAILED")
        row["message"] = _message(result)
    except Exception as exc:  # noqa: BLE001 - 한 질문의 실패가 수집을 멈추지 않는다
        row["error"] = f"{type(exc).__name__}: {exc}"
    row["latency_ms"] = round((time.perf_counter() - started) * 1_000, 1)
    return row


async def collect(
    cases: Sequence[QuestionCase], *, adapter: LifeCapabilityAdapter, log: Callable[[str], None] = print
) -> list[dict[str, Any]]:
    rows = []
    for index, case in enumerate(cases, start=1):
        row = await run_question(adapter, case)
        rows.append(row)
        life = row["results"][0]["status"] if row["results"] else "-"
        log(f"  [{index:>3}/{len(cases)}] {case.question_id:<40} {row['status']:<9} life={life} {row['latency_ms']:.0f}ms")
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Life 직접 수집 (#343)")
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--label", required=True, help="answers_<label>.jsonl")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--fake", action="store_true", help="가짜 ask — DB·모델 없이 모양만")
    args = parser.parse_args(argv)

    cases = load_questions(args.questions)
    chosen = cases[: args.limit] if args.limit else cases
    adapter = LifeCapabilityAdapter(ask=_fake_ask) if args.fake else LifeCapabilityAdapter()
    started = utc_now()
    print(f"Life 직접 수집 · {len(chosen)}/{len(cases)}문항 · {'가짜' if args.fake else '실제'} 어댑터")
    rows = asyncio.run(collect(chosen, adapter=adapter))
    meta = {
        "label": args.label, "adapters": "life-direct", "flag": None,
        "questions_file": args.questions.name, "questions_sha256": file_sha256(args.questions),
        "question_count": len(rows), "requested_count": len(cases), "limited": bool(args.limit),
        "started_at": started, "finished_at": utc_now(), "provenance": source_provenance(),
    }
    out = args.out or answers_path(args.label)
    write_answers(out, rows, meta=meta)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    print(f"저장 {out} · 상태 {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
