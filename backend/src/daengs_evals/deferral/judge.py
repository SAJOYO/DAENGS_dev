"""물러섬 축 — 채점과 리포트.

    uv run python -m daengs_evals.deferral.judge score  --cells pf_v1_1 --variant A
    uv run python -m daengs_evals.deferral.judge report --cells pf_v1_1

`score` 는 셀 하나(답변 하나)를 단위로 한다 — 쌍이 아니다. 거절된 셀은 **모델을 안 부르고**
`refusal.code` 로 읽고, 답한 셀만 판정기에 보낸다. 같은 답이 여러 셀에 있으면(잡음 · 옮겨 심음)
문장이 같은 것은 한 번만 판정한다 — 결정론이라 같은 답이고, 두 번 부르는 건 토큰 낭비다.

축 A 의 셀 파일을 그대로 읽는다. 질문마다 기대(`expectations_*.jsonl`)가 있어야 하고, 기대가
없는 질문은 `unlabeled` 로 센다 — 조용히 빠지지 않는다.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from daengs_evals.answer_quality.gemini import (
    DEFAULT_TOKEN_BUDGET,
    TokenBudgetExceeded,
    TokenLedger,
)
from daengs_evals.answer_quality.provenance import source_provenance, utc_now
from daengs_evals.calibration.hygiene import require_judge_hygiene
from daengs_evals.calibration.openai_client import client as openai_client
from daengs_evals.calibration.openai_client import generate_structured
from daengs_evals.calibration.stats import wilson_interval
from daengs_evals.deferral.rubric import (
    ASSETS_DIR,
    PROMPT_VERSIONS,
    TEMPERATURE,
    DeferralVerdict,
    build_prompt,
    confusion,
    load_expectations,
    move_from_cell,
    outcome,
    refusal_code,
)
from daengs_evals.profile_fitness.collect import cells_path, load_cells
from daengs_evals.profile_fitness.questions import load_questions


def expectations_path(cells_label: str) -> Path:
    # 질문 셋은 cells meta 의 questions_path 를 따른다 — pf_v1 · pf_v1_1 이 같은 파일을 가리키면 같은 기대
    return ASSETS_DIR / f"expectations_{cells_label}.jsonl"


def judgments_path(cells_label: str, variant: str) -> Path:
    return ASSETS_DIR / f"judgments_{cells_label}_{PROMPT_VERSIONS[variant]}.jsonl"


def _generation_models() -> list[str]:
    from daengs_backend.orchestration.adapters.general import GENERAL_MODEL_ID
    from daengs_backend.orchestration.semantic import ROUTER_MODEL_ID

    return sorted({ROUTER_MODEL_ID, GENERAL_MODEL_ID})


def _resolve_expectations(cells_label: str, questions_path: Path) -> Path:
    p = expectations_path(cells_label)
    if p.exists():
        return p
    # 같은 질문 파일을 쓰는 다른 label 의 기대를 빌린다 (pf_v1 → pf_v1_1, pf_v6_sub18 → pf_v7_sub18 처럼).
    # 2026-09-12 까지는 글롭의 **첫 파일**을 돌려줘서 pf_v7_sub18 이 pf_ask_v1 의 기대를 받아 40셀이 전부
    # "라벨 없음" 으로 적혔다 — 질문 id 를 실제로 덮는 파일만 고른다.
    wanted = {q.question_id for q in load_questions(questions_path)}
    best: tuple[int, Path] | None = None
    for cand in sorted(ASSETS_DIR.glob("expectations_*.jsonl")):
        covered = len(wanted & set(load_expectations(cand)))
        if covered and (best is None or covered > best[0]):
            best = (covered, cand)
    if best is None:
        raise FileNotFoundError(
            f"기대 라벨이 없다: {p.name} (질문 {len(wanted)}개를 덮는 기대 파일이 없다)"
        )
    return best[1]


def run_score(*, cells_label: str, model: str, variant: str, budget: int, log=print) -> Path:
    meta, cells = load_cells(cells_path(cells_label))
    questions = {q.question_id: q for q in load_questions(Path(meta["questions_path"]))}
    exp = load_expectations(_resolve_expectations(cells_label, Path(meta["questions_path"])))
    hygiene = require_judge_hygiene(model, _generation_models())
    ledger = TokenLedger(budget=budget, log=log)
    cli = openai_client()
    path = judgments_path(cells_label, variant)
    path.parent.mkdir(parents=True, exist_ok=True)

    from daengs_evals.profile_fitness.collect import real_capabilities as _real_caps

    real_capabilities = _real_caps(meta.get("adapters"))
    # 이미 판정한 답은 다시 안 부른다 — 같은 label 의 이전 판정 파일에서 (문장이 같으면 같은 답)
    cache: dict[tuple[str, str], DeferralVerdict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                old = json.loads(line)
                if old.get("kind") == "judgment" and old.get("verdict") and old.get("message_key"):
                    cache[(old["question_id"], old["message_key"])] = (
                        DeferralVerdict.model_validate(old["verdict"])
                    )
    rows: list[dict[str, Any]] = []
    judged = 0
    budget_hit = False
    for c in cells:
        qid = c["question_id"]
        q = questions[qid]
        e = exp.get(qid)
        row: dict[str, Any] = {
            "kind": "judgment",
            "cell": f"{qid}|{c['arm']}#{c['run']}",
            "question_id": qid,
            "status": c["status"],
            "refusal_code": refusal_code(c),
            "expect": e.expect if e else None,
            "expected_reason": e.expected_reason if e else None,
            "review": e.review if e else None,
        }
        verdict: DeferralVerdict | None = None
        answered_by = next(
            (r.get("capability") for r in c.get("results") or [] if r.get("status") == "OK"), None
        )
        if (
            real_capabilities is not None
            and answered_by is not None
            and answered_by not in real_capabilities
            and row["refusal_code"]
            is None  # 응급 게이트(vet_contact)는 코드 거절 — 가짜 어댑터여도 잰 것
        ):
            # 가짜 어댑터의 자리표시 답 — 물러섬을 잰 것이 아니라 못 잰 것이다
            row.update(
                move="unmeasured", outcome="unmeasured_fake_adapter", answered_by=answered_by
            )
            rows.append(row)
            log(f"  {row['cell']:44s} 미측정 ({answered_by} 가짜 어댑터)")
            continue
        if row["refusal_code"] is None and c["status"] in ("ANSWERED", "PARTIAL"):
            key = (qid, (c.get("message") or "").strip())
            row["message_key"] = key[1]
            if key not in cache:
                try:
                    cache[key] = _judge_one(
                        model=model,
                        prompt=build_prompt(question=q.query, answer=c["message"], variant=variant),
                        ledger=ledger,
                        cli=cli,
                        label=row["cell"],
                    )
                except TokenBudgetExceeded as exc:
                    # 여기까지의 판정을 **파일에 남기고** 멈춘다 — 2026-09-11 에 예산 초과가 예외로 튀어
                    # 2만 토큰어치 판정이 통째로 사라졌다. 같은 명령을 다시 돌리면 캐시로 이어서 한다.
                    log(
                        f"⚠ {exc} — 지금까지 {judged}건 판정은 파일에 남긴다. 같은 명령으로 이어서 하세요"
                    )
                    budget_hit = True
                    break
                judged += 1
            verdict = cache[key]
            row["verdict"] = verdict.model_dump(mode="json")
        move, reason = move_from_cell(c, verdict)
        row["move"] = move
        row["outcome"] = outcome(e, move, reason) if e else "unlabeled"
        rows.append(row)
        log(f"  {row['cell']:44s} {move:9s} {row['outcome']}")

    _write_judgments(
        path,
        cells_label=cells_label,
        model=model,
        variant=variant,
        hygiene=hygiene,
        judged=judged,
        ledger=ledger,
        rows=rows,
        partial=budget_hit,
    )
    log(
        f"셀 {len(rows)} · 판정 호출 {judged} · 토큰 {ledger.input_tokens}/{ledger.output_tokens} → {path.name}"
        + (" (예산에서 멈춤 — 부분)" if budget_hit else "")
    )
    return path


def _judge_one(
    *, model: str, prompt: str, ledger: TokenLedger, cli: Any, label: str
) -> DeferralVerdict:
    return generate_structured(
        model=model,
        prompt=prompt,
        schema=DeferralVerdict,
        temperature=TEMPERATURE,
        ledger=ledger,
        cli=cli,
        label=label,
    )


def _write_judgments(
    path: Path,
    *,
    cells_label: str,
    model: str,
    variant: str,
    hygiene: Any,
    judged: int,
    ledger: TokenLedger,
    rows: list[dict[str, Any]],
    partial: bool,
) -> None:
    with path.open("w", encoding="utf-8") as h:
        h.write(
            json.dumps(
                {
                    "kind": "meta",
                    "cells_label": cells_label,
                    "judge_model": model,
                    "variant": variant,
                    "prompt_version": PROMPT_VERSIONS[variant],
                    "hygiene": hygiene,
                    "unique_answers_judged": judged,
                    "partial": partial,
                    "tokens": {"input": ledger.input_tokens, "output": ledger.output_tokens},
                    "started_at": utc_now(),
                    "source": source_provenance(),
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        for r in rows:
            h.write(json.dumps(r, ensure_ascii=False) + "\n")


CONSISTENCY_REPEATS = 3
CONSISTENCY_SAMPLE = 6  # 6답 × 3회 = 18호출 ≈ 1.5만 토큰


def run_consistency(
    *,
    cells_label: str,
    model: str,
    variant: str,
    budget: int,
    only_questions: list[str] | None = None,
    log=print,
) -> Path:
    """답한 셀 몇 개를 같은 프롬프트로 3번 판정한다. `answer_move` 가 흔들리면 그 답은 사람 큐다.

    실측(2026-09-09): pf_v1 과 pf_v1_1 은 같은 답변인데 '밥 안 먹는데 기다려도 되나' 가 한 번은
    deferred, 한 번은 mixed 로 갈렸다. 단일 답변 판정기라 위치 편향은 없지만 자기일관성은 재야 한다.
    """
    meta, cells = load_cells(cells_path(cells_label))
    questions = {q.question_id: q for q in load_questions(Path(meta["questions_path"]))}
    real = {"general"} if meta.get("adapters") == "fallback-only" else None
    hygiene = require_judge_hygiene(model, _generation_models())
    ledger = TokenLedger(budget=budget, log=log)
    cli = openai_client()

    seen: set[str] = set()
    sample = []
    for c in cells:
        by = next(
            (r.get("capability") for r in c.get("results") or [] if r.get("status") == "OK"), None
        )
        if c["status"] != "ANSWERED" or (real and by not in real) or c["question_id"] in seen:
            continue
        if only_questions and c["question_id"] not in only_questions:
            continue
        seen.add(c["question_id"])
        sample.append(c)
        if len(sample) >= CONSISTENCY_SAMPLE:
            break

    results = []
    for c in sample:
        moves = []
        notes = []
        for _ in range(CONSISTENCY_REPEATS):
            v = generate_structured(
                model=model,
                prompt=build_prompt(
                    question=questions[c["question_id"]].query, answer=c["message"], variant=variant
                ),
                schema=DeferralVerdict,
                temperature=TEMPERATURE,
                ledger=ledger,
                cli=cli,
                label=c["question_id"],
            )
            moves.append(v.answer_move)
            notes.append(v.note)
        results.append(
            {
                "question_id": c["question_id"],
                "moves": moves,
                "consistent": len(set(moves)) == 1,
                "notes": notes,
            }
        )
        log(f"  {c['question_id']:28s} {moves} {'' if len(set(moves)) == 1 else '← 흔들림'}")

    rate = sum(1 for r in results if r["consistent"]) / max(1, len(results))
    record = {
        "cells_label": cells_label,
        "judge_model": model,
        "variant": variant,
        "prompt_version": PROMPT_VERSIONS[variant],
        "hygiene": hygiene,
        "repeats": CONSISTENCY_REPEATS,
        "sample": len(results),
        "consistency_rate": rate,
        "results": results,
        "tokens": {"input": ledger.input_tokens, "output": ledger.output_tokens},
        "checked_at": utc_now(),
        "source": source_provenance(),
    }
    tag = "_targeted" if only_questions else ""
    path = ASSETS_DIR / f"consistency_{cells_label}_{PROMPT_VERSIONS[variant]}{tag}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"자기일관성 {rate:.2f} ({len(results)}답 × {CONSISTENCY_REPEATS}회) → {path.name}")
    return path


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    labeled = [r for r in rows if r["outcome"] not in ("unlabeled", "unmeasured_fake_adapter")]
    by_reason: dict[str, Counter] = defaultdict(Counter)
    for r in labeled:
        by_reason[r["expected_reason"] or "none"][r["outcome"]] += 1
    c = confusion(r["outcome"] for r in labeled)
    over_n = (
        c["correct_answer"] + c["over_ask"] + c["correct_ask"] + c["under_ask"] + c["over_refusal"]
    )
    under_n = c["correct_defer"] + c["wrong_reason"] + c["under_refusal"]
    over_ask_n = c["correct_answer"] + c["over_ask"]
    under_ask_n = c["correct_ask"] + c["under_ask"]
    return {
        "n_cells": len(rows),
        "unlabeled": sum(1 for r in rows if r["outcome"] == "unlabeled"),
        "unmeasured_fake_adapter": sum(
            1 for r in rows if r["outcome"] == "unmeasured_fake_adapter"
        ),
        "review_pending": sum(1 for r in labeled if r.get("review")),
        "confusion": c,
        "over_refusal": wilson_interval(c["over_refusal"], over_n).as_dict() if over_n else None,
        "under_refusal": wilson_interval(c["under_refusal"], under_n).as_dict()
        if under_n
        else None,
        "over_ask": wilson_interval(c["over_ask"], over_ask_n).as_dict() if over_ask_n else None,
        "under_ask": wilson_interval(c["under_ask"], under_ask_n).as_dict()
        if under_ask_n
        else None,
        "by_expected_reason": {k: dict(v) for k, v in by_reason.items()},
        "moves": dict(Counter(r["move"] for r in labeled)),
        "over_refusal_cases": [r["cell"] for r in labeled if r["outcome"] == "over_refusal"],
        "under_refusal_cases": [r["cell"] for r in labeled if r["outcome"] == "under_refusal"],
        "wrong_reason_cases": [r["cell"] for r in labeled if r["outcome"] == "wrong_reason"],
        "over_ask_cases": [r["cell"] for r in labeled if r["outcome"] == "over_ask"],
        "under_ask_cases": [r["cell"] for r in labeled if r["outcome"] == "under_ask"],
    }


def render(s: dict[str, Any], meta: dict[str, Any]) -> str:
    c = s["confusion"]
    pct = lambda d: (
        f"{d['point'] * 100:.0f}% [{d['low'] * 100:.0f}, {d['high'] * 100:.0f}] (n={d['n']})"
        if d
        else "—"
    )
    lines = [
        f"# 물러섬 정확도 — `{meta['cells_label']}`",
        "",
        f"판정 {meta['judge_model']} · {meta['prompt_version']} · 고유 답변 {meta['unique_answers_judged']}건 판정 · {utc_now()}",
        "",
        (
            f"> 잠정. 기대 라벨 중 사람 검토 대기 {s['review_pending']}건. 사람 라벨 κ 전. "
            f"가짜 어댑터가 답한 셀 {s['unmeasured_fake_adapter']}개는 미측정."
        ),
        "",
        "## 양방향 — 총계 하나로 줄이지 않는다",
        "",
        "| 방향 | 비율 [95% CI] |",
        "| --- | --- |",
        f"| **과잉거절** (답하거나 되물어야 하는데 넘김) | {pct(s['over_refusal'])} |",
        f"| **과소거절** (넘겨야 하는데 답하거나 되물음) | {pct(s['under_refusal'])} |",
        f"| **과잉되묻기** (답할 수 있는데 되물음) | {pct(s.get('over_ask'))} |",
        f"| **과소되묻기** (관찰 없이 답함) | {pct(s.get('under_ask'))} |",
        "",
        "| 결과 | 건수 |",
        "| --- | --- |",
        *[
            f"| {k} | {c[k]} |"
            for k in (
                "correct_answer",
                "over_ask",
                "over_refusal",
                "correct_ask",
                "under_ask",
                "correct_defer",
                "wrong_reason",
                "under_refusal",
            )
        ],
        "",
        f"움직임 분포: {s['moves']}",
        "",
        "## 사례",
        "",
        f"- 과잉거절: {s['over_refusal_cases'] or '없음'}",
        f"- 과소거절: {s['under_refusal_cases'] or '없음'}",
        f"- 엉뚱한 사유: {s['wrong_reason_cases'] or '없음'}",
        f"- 과잉되묻기: {s.get('over_ask_cases') or '없음'}",
        f"- 과소되묻기: {s.get('under_ask_cases') or '없음'}",
        "",
        "## 기대 사유별",
        "",
        *[f"- {k}: {v}" for k, v in s["by_expected_reason"].items()],
        "",
    ]
    return "\n".join(lines)


def run_report(*, cells_label: str, variant: str) -> Path:
    path = judgments_path(cells_label, variant)
    meta: dict[str, Any] | None = None
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            (rows.append(r) if r.get("kind") == "judgment" else None)
            if r.get("kind") == "meta":
                meta = r
    assert meta is not None
    s = summarize(rows)
    md = ASSETS_DIR / f"report_deferral_{cells_label}.md"
    js = ASSETS_DIR / f"summary_deferral_{cells_label}.json"
    md.write_text(render(s, meta), encoding="utf-8")
    js.write_text(json.dumps({**s, "meta": meta}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(render(s, meta))
    print(f"→ {md.name} · {js.name}")
    return md


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="물러섬 축")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("score")
    p.add_argument("--cells", required=True)
    p.add_argument("--variant", choices=("A", "B"), default="A")
    p.add_argument("--judge-model", default=None)
    p.add_argument("--token-budget", type=int, default=DEFAULT_TOKEN_BUDGET)
    r = sub.add_parser("report")
    r.add_argument("--cells", required=True)
    r.add_argument("--variant", choices=("A", "B"), default="A")
    k = sub.add_parser("consistency")
    k.add_argument("--cells", required=True)
    k.add_argument("--variant", choices=("A", "B"), default="A")
    k.add_argument("--judge-model", default=None)
    k.add_argument("--token-budget", type=int, default=DEFAULT_TOKEN_BUDGET)
    k.add_argument(
        "--questions", nargs="*", default=None, help="이 질문들만 (흔들렸던 답을 겨냥할 때)"
    )
    args = parser.parse_args(argv)
    if args.command == "score":
        from daengs_backend.config import settings

        run_score(
            cells_label=args.cells,
            model=args.judge_model or settings.openai_judge_model,
            variant=args.variant,
            budget=args.token_budget,
        )
    elif args.command == "consistency":
        from daengs_backend.config import settings

        run_consistency(
            cells_label=args.cells,
            model=args.judge_model or settings.openai_judge_model,
            variant=args.variant,
            budget=args.token_budget,
            only_questions=args.questions,
        )
    else:
        run_report(cells_label=args.cells, variant=args.variant)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
