"""훈련 RAG judge 명령 (D-060).

    uv run python -m tools.training_quality check-anchors
    uv run python -m tools.training_quality score --label lap1
    uv run python -m tools.training_quality review --label lap1
    uv run python -m tools.training_quality agreement --label lap1__codex --against lap1

**`score` 는 `check-anchors` 가 통과한 기록이 없으면 안 돈다.** judge 를 믿을 근거가 앵커뿐이라
그 게이트를 코드로 든다 (#277 의 앵커 게이트와 같은 자리). 앵커 기록은 **judge 모델 · 프롬프트
버전별로** 남는다 — 둘 중 하나가 바뀌면 다시 통과해야 한다.

⚠ **사람 라벨은 진행하지 않는다** (D-060 ⑦, 2026-09-07). `RAG-007` 이 요구한 30개가 생기지
않으므로 **`score` 의 수는 지표가 아니고 앞으로도 아니다.** 이 도구의 쓰임은 `review` 다 —
사람이 볼 자리를 고르는 것. 교차검증 프롬프트는 `docs/training/judge_codex_handoff.md` 에 있다.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tools.training_quality import anchors as anchors_mod
from tools.training_quality import collect as collect_mod
from tools.training_quality import judge as judge_mod


def _anchor_record_path(model: str, prompt_version: int) -> Path:
    safe = model.replace("/", "_")
    return collect_mod.ASSETS_DIR / f"anchor_check__{safe}__v{prompt_version}.json"


def _judgments_path(label: str) -> Path:
    return collect_mod.ASSETS_DIR / f"judgments_{label}.jsonl"


def _model_name(override: str | None) -> str:
    if override:
        return override
    from daengs_backend.config import settings

    return settings.openai_judge_model


def cmd_check_anchors(args: argparse.Namespace) -> int:
    model = _model_name(args.judge_model)
    result = anchors_mod.check(model=model)
    for row in result["results"]:
        mark = "OK  " if row["passed"] else "FAIL"
        print(f"  [{mark}] {row['id']:<22} 기대={row['expected_grounded']} 실제={row['actual_grounded']}")
        if row["missing_reasons"]:
            print(f"           judge 가 못 짚은 근거: {row['missing_reasons']}")
        if not row["passed"]:
            print(f"           unsupported: {row['unsupported']}")

    path = _anchor_record_path(model, judge_mod.PROMPT_VERSION)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({**result, "judge_model": model}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n앵커 {result['n_passed']}/{result['n']} 통과 → {path}")
    if not result["passed"]:
        print("⚠ 통과하지 못했습니다. 프롬프트를 고치고 PROMPT_VERSION 을 올린 뒤 다시 도세요.")
        return 1
    return 0


def _require_anchor_gate(model: str) -> None:
    path = _anchor_record_path(model, judge_mod.PROMPT_VERSION)
    if not path.exists():
        raise SystemExit(
            f"앵커 기록이 없습니다: {path}\n"
            f"  먼저 `check-anchors --judge-model {model}` 을 도세요 — 사람 라벨이 모이기 전까지"
            " judge 를 믿을 근거는 앵커뿐입니다 (D-060 ⑥)."
        )
    record = json.loads(path.read_text(encoding="utf-8"))
    if not record.get("passed"):
        raise SystemExit(f"앵커가 통과하지 못한 기록입니다: {path}")


def cmd_score(args: argparse.Namespace) -> int:
    model = _model_name(args.judge_model)
    _require_anchor_gate(model)

    dump = args.dump or collect_mod.dump_path(args.label)
    head, rows = collect_mod.load_dump(dump)
    if args.limit:
        rows = rows[: args.limit]

    def show(judgment: judge_mod.Judgment) -> None:
        mark = "grounded" if judgment.grounded else f"NOT ({len(judgment.unsupported)})"
        print(f"  {judgment.id:<16} {mark}")

    judgments = judge_mod.judge_rows(rows, model=model, on_item=show)
    header = judge_mod.header(args.label, judgments, model=model)

    out = _judgments_path(args.label)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        fh.write(header.model_dump_json() + "\n")
        for judgment in judgments:
            fh.write(judgment.model_dump_json() + "\n")

    summary = judge_mod.summarise(judgments)
    print(f"\n{out}")
    print(f"  덤프 {head.get('items')}문항 중 판정 {summary['n']}건 "
          f"(ANSWER 가 아니거나 청크가 없는 행은 뺍니다 — D-060 ⑤)")
    print(f"  grounded {summary['grounded']} · 아님 {summary['not_grounded']}")
    print(f"  사람이 봐야 할 문항 {summary['review_needed']}건  → `review --label {args.label}`")
    print("\n⚠ 이 수는 지표가 아닙니다. 사람 라벨을 진행하지 않기로 해(D-060 ⑦) 승격 경로가")
    print("   닫혔습니다 — 이 비율을 어디에도 올리지 마세요.")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    """사람이 라벨링할 문항을 고른다. **judge 가 자신 없어 하는 자리**를 고른다."""
    _, rows = collect_mod.load_dump(args.dump or collect_mod.dump_path(args.label))
    judgments = _load_judgments(_judgments_path(args.label))
    picks = judge_mod.disagreements(judgments, rows)
    if not picks:
        print("판정 내부에 모순이나 경계선이 없습니다 — 무작위 표본으로 라벨링하세요.")
        return 0
    for pick in picks:
        print(f"\n── {pick['id']}  ({pick['why_review']})")
        print(f"   질문: {pick['question']}")
        print(f"   judge: grounded={pick['grounded']}  unsupported={pick['unsupported']}")
        print(f"   근거: {pick['rationale']}")
    print(f"\n{len(picks)}건 — **이것이 이 도구의 산출물입니다.** 사람이 여기부터 보면 됩니다.")
    print("   교차검증을 붙이려면 docs/training/judge_codex_handoff.md 의 프롬프트 ① 을 쓰세요.")
    print("   ⚠ 판정 파일의 judge_model 에는 **실제 모델명**을 적으세요. 'human' 으로 쓰면")
    print("      나중에 사람 라벨과 구분되지 않습니다 (D-060 ⑦).")
    return 0


def _load_judgments(path: Path) -> list[judge_mod.Judgment]:
    if not path.exists():
        raise SystemExit(f"판정 파일이 없습니다: {path}  — 먼저 `score` 를 도세요.")
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [judge_mod.Judgment.model_validate_json(ln) for ln in lines[1:]]


def cmd_agreement(args: argparse.Namespace) -> int:
    reference = _load_judgments(_judgments_path(args.against))
    candidate = _load_judgments(_judgments_path(args.label))
    result = judge_mod.agreement(reference, candidate)
    print(f"일치 {result['agreed']}/{result['n']}   "
          f"(분모는 **양쪽에 다 있는 문항**입니다 — RAG-075 ①)")
    for row in result["mismatch"]:
        print(f"\n── {row['id']}  기준={row['reference']} 후보={row['candidate']}")
        print(f"   기준 근거: {row['reference_why']}")
        print(f"   후보가 짚은 것: {row['candidate_unsupported']}")
    print("\n⚠ 이것은 **판정자 간 일치율**이지 캘리브레이션이 아닙니다 (D-060 ⑦).")
    print("   LLM 둘이 일치하는 것은 둘이 같은 맹점을 공유하는 것일 수도 있습니다.")
    print("   특히 검증자가 GPT 계열이면 judge 와 같은 계열이라, D-060 ① 이 일부러 갈라 둔")
    print("   self-preference 분리가 무너져 이 수가 부풀려집니다. 지표로 승격하지 마세요.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools.training_quality", description=__doc__)
    parser.add_argument("--judge-model", default=None, help="기본은 settings.openai_judge_model")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check-anchors", help="judge 가 쓸 만한지 잰다 (score 의 전제)")

    p_score = sub.add_parser("score", help="덤프를 채점한다")
    p_score.add_argument("--label", required=True)
    p_score.add_argument("--dump", type=Path, default=None)
    p_score.add_argument("--limit", type=int, default=0)

    p_review = sub.add_parser("review", help="사람이 라벨링할 문항을 고른다")
    p_review.add_argument("--label", required=True)
    p_review.add_argument("--dump", type=Path, default=None)

    p_agree = sub.add_parser("agreement", help="두 판정 파일의 일치율")
    p_agree.add_argument("--label", required=True)
    p_agree.add_argument("--against", required=True, help="교차검증 상대 (보통 <label>__codex)")

    args = parser.parse_args(argv)
    handlers: dict[str, Any] = {
        "check-anchors": cmd_check_anchors,
        "score": cmd_score,
        "review": cmd_review,
        "agreement": cmd_agreement,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
