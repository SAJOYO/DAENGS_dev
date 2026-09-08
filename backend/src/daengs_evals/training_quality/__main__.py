"""훈련 RAG judge 명령 (D-060).

    uv run python -m daengs_evals.training_quality check-anchors
    uv run python -m daengs_evals.training_quality score --label lap1
    uv run python -m daengs_evals.training_quality review --label lap1
    uv run python -m daengs_evals.training_quality agreement --label lap1__codex --against lap1

**`score` 는 `check-anchors` 가 통과한 기록이 없으면 안 돈다.** judge 를 믿을 근거가 앵커뿐이라
그 게이트를 코드로 든다 (#277 의 앵커 게이트와 같은 자리). 앵커 기록은 **judge 모델 · 프롬프트
버전별로** 남는다 — 둘 중 하나가 바뀌면 다시 통과해야 한다.

⚠ **승격은 포기했다** (D-060 ⑨, 2026-09-07). `score` 의 grounded 비율은 지표가 아니고 앞으로도
아니다. 이 도구의 산출물은 **`review` 와 `blindspots`** 다 — 사람이 볼 자리를 고르는 것.

첫날 결함 둘을 찾았고 **둘 다 비율이 아니라 선별에서** 나왔다: 프롬프트 오탐은 앵커 게이트가,
judge 의 축 넘기(⑧)는 `blindspots` 가 잡았다. 일치율은 아무것도 못 찾았다.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from daengs_evals.training_quality import anchors as anchors_mod
from daengs_evals.training_quality import collect as collect_mod
from daengs_evals.training_quality import judge as judge_mod


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
            f"  먼저 `check-anchors --judge-model {model}` 을 도세요 — judge 를 믿을 근거는"
            " 앵커뿐입니다 (D-060 ⑥). 사람 라벨은 진행하지 않습니다 (⑦)."
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
    print("\n⚠ 이 비율은 지표가 아닙니다. **승격은 포기했습니다** (D-060 ⑨) — 어디에도")
    print("   올리지 마세요. `score` 는 산출물이 아니라 아래 둘을 위한 재료입니다.")
    print(f"   → `review --label {args.label}`      판정이 흔들린 자리")
    print(f"   → `blindspots --label {args.label} --against <상대>`  둘 다 안 훑은 자리")
    return 0


def _review_sheet(label: str, picks: list[dict[str, Any]],
                  rows: list[dict[str, Any]]) -> str:
    """사람이 실제로 검산할 수 있는 한 장. **청크 본문을 같이 싣는 것이 요점이다.**

    판정만 보여주면 사람이 할 수 있는 것은 "그럴듯한가" 뿐이고, 그건 `RAG-075` ② 가 실패한
    바로 그 판단이다. 답변과 자료를 나란히 놓아야 *"이 문장이 자료에 있나"* 라는 **확인 가능한
    질문**으로 바뀐다.
    """
    by_id = {row.get("id"): row for row in rows}
    out = [
        f"# 사람이 볼 자리 — `{label}`",
        "",
        (f"judge 가 판정 {len(rows)}건 중 **{len(picks)}건**에서 흔들렸습니다. 전부 읽지 "
         "마시고 여기부터 보세요."),
        "",
        "## 각 문항에서 물을 것 하나",
        "",
        "> **judge 가 뒷받침 안 된다고 한 그 문장이, 아래 [자료]로 정말 뒷받침이 안 됩니까?**",
        "",
        ("그것만 보시면 됩니다. 답변이 좋은지 나쁜지는 이 축이 묻는 것이 아닙니다. "
         "`Ctrl+F` 로 낱말을 찾아보는 것이 가장 빠릅니다."),
        "",
        ("⚠ **«없다»와 «뒷받침이 안 된다»는 다릅니다.** judge 가 «자료에는 있지만 다른 맥락의 "
         "것이라 이 질문에 옮겨 쓸 수 없다»고 하는 경우가 있습니다. 그때는 낱말을 찾으면 "
         "**나옵니다** — 그래도 판정은 false 입니다. 아래 근거를 꼭 읽으세요."),
        "",
        ("⚠ 그리고 그 경우 **judge 가 축을 넘은 것일 수 있습니다.** 이 축은 *"
         "«자료에 있는가»* 만 묻고, *«이 질문에 맞는 답인가»* 는 다른 자가 잽니다"
         "(#305 의 `answers_question`). 자료에 있는데 «맥락이 다르다»는 이유로 false 라면 "
         "그것은 이 축의 판정이 아닙니다 — 그렇게 보이면 적어 주세요."),
        "",
        "판정이 셋 중 하나로 갈립니다:",
        "",
        "| | 뜻 |",
        "| --- | --- |",
        "| **judge 가 맞다** | 그 문장이 자료에 없다 — 생성부가 자료 밖으로 나갔다 |",
        "| **judge 가 틀렸다** | 자료에 있는데 못 찾았다, 또는 표현만 다른 것을 없다고 했다 |",
        "| **자료가 문제다** | 답은 맞는데 검색된 네 청크에 근거가 안 잡혔다 (검색 문제) |",
        "",
        ("⚠ 셋째가 있다는 것이 중요합니다. judge 가 틀린 것도 생성이 틀린 것도 아니라 "
         "**검색이 엉뚱한 청크를 물어온** 경우이고, 그건 고칠 곳이 다릅니다."),
        "",
        "---",
        "",
    ]
    for i, pick in enumerate(picks, start=1):
        row = by_id.get(pick["id"]) or {}
        out += [
            f"## {i}. `{pick['id']}` — {pick['why_review']}",
            "",
            f"**질문** {pick['question']}",
            "",
            "### judge 가 뒷받침 안 된다고 한 것 — 근거를 꼭 같이 읽으세요",
            "",
        ]
        out += [f"- {claim}" for claim in pick["unsupported"]] or ["- (없음)"]
        out += ["", f"> {pick['rationale']}", "", "### 답변 전문", "",
                "```", str(row.get("answer", "")).strip(), "```", "",
                "### 검색된 자료 — 여기에 있습니까?", ""]
        for n, chunk in enumerate(row.get("chunks") or [], start=1):
            head = " › ".join(str(p) for p in (chunk.get("heading_path") or []))
            out += [
                f"<details><summary><b>[자료 {n}]</b> {chunk.get('document_id', '?')}"
                + (f" — {head}" if head else "")
                + f" (score {chunk.get('score', 0):.3f})</summary>",
                "",
                "```",
                str(chunk.get("text", "")).strip(),
                "```",
                "",
                "</details>",
                "",
            ]
        out += ["---", ""]
    out += [
        "## 다 보신 뒤에",
        "",
        "판정을 판정 파일과 **같은 모양**으로 적어 두시면 `agreement` 가 견줍니다.",
        "",
        "```",
        f"backend/evals/training_quality/judgments_{label}__<이름>.jsonl",
        "```",
        "",
        ("⚠ `judge_model` 에는 **누가 판정했는지**를 적으세요 — 사람이면 `human`, LLM 이면 "
         "실제 모델명입니다. 섞이면 나중에 구분되지 않습니다 (D-060 ⑦)."),
        "",
        ("⚠ 이 대조로 나오는 수는 **지표가 아닙니다.** 사람 라벨 30개(RAG-007) 조건이 "
         "충족되지 않기 때문입니다. 쓸모는 *judge 가 어느 쪽으로 기우는지* 까지입니다."),
    ]
    return "\n".join(out) + "\n"


def cmd_review(args: argparse.Namespace) -> int:
    """사람이 볼 문항을 고른다. **judge 가 자신 없어 하는 자리**를 고른다."""
    _, rows = collect_mod.load_dump(args.dump or collect_mod.dump_path(args.label))
    judgments = _load_judgments(_judgments_path(args.label))
    picks = judge_mod.disagreements(judgments, rows)
    if not picks:
        print("판정 내부에 모순이나 경계선이 없습니다 — 무작위 표본으로 보세요.")
        return 0
    for pick in picks:
        print(f"\n── {pick['id']}  ({pick['why_review']})")
        print(f"   질문: {pick['question']}")
        print(f"   judge: grounded={pick['grounded']}  unsupported={pick['unsupported']}")
        print(f"   근거: {pick['rationale']}")

    out = args.out or (collect_mod.ASSETS_DIR / f"review_{args.label}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_review_sheet(args.label, picks, rows), encoding="utf-8")

    print(f"\n{len(picks)}건 — **이것이 이 도구의 산출물입니다.**")
    print(f"   사람이 읽을 한 장: {out}")
    print("   답변과 검색된 자료를 나란히 실어 뒀습니다 — 판정만 보면 «그럴듯한가»밖에 못 묻고,")
    print("   그것이 RAG-075 ② 가 실패한 바로 그 판단입니다.")
    print("   교차검증을 붙이려면 docs/training/judge_codex_handoff.md 의 프롬프트 ① 을 쓰세요.")
    return 0


def _load_judgments(path: Path) -> list[judge_mod.Judgment]:
    if not path.exists():
        raise SystemExit(f"판정 파일이 없습니다: {path}  — 먼저 `score` 를 도세요.")
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [judge_mod.Judgment.model_validate_json(ln) for ln in lines[1:]]


def _judge_model(label: str) -> str:
    """판정 파일의 헤더가 적은 판정자. **사람인지 LLM 인지로 이 수의 뜻이 갈린다.**"""
    lines = _judgments_path(label).read_text(encoding="utf-8").splitlines()
    return str(json.loads(lines[0]).get("judge_model", "?")) if lines else "?"


def cmd_blindspots(args: argparse.Namespace) -> int:
    """두 판정자가 **공통으로 확인 목록에 안 올린** 문장을 찾는다.

    `t03`↔`t19` 를 드러낸 것이 이 대조다 (`judge_lap1_0907.md`). 일치율은 판정자 둘이 같은
    결론을 냈는지만 말하고 **둘 다 안 본 자리**는 말하지 않는다 — 오탐이 숨는다면 거기다.
    Codex 교차검증에서 일치율(15/15)은 아무것도 못 냈고, 값은 이 대조에서 나왔다.

    ⚠ **글자 겹침으로 짐작한 값이라 단서이지 증거가 아니다.** 겹침이 낮아도 판정자가 같은
    내용을 다른 말로 적었을 수 있다. 이 표는 *"어디부터 볼까"* 만 정한다.
    """
    import re

    def bigrams(text: str) -> set[str]:
        flat = re.sub(r"[^가-힣0-9a-zA-Z]", "", text)
        return {flat[i : i + 2] for i in range(len(flat) - 1)}

    def covered(sentence: str, claims: list[str]) -> float:
        base = bigrams(sentence)
        if not base:
            return 1.0
        return max((len(base & bigrams(c)) / len(base) for c in claims), default=0.0)

    def sentences(text: str) -> list[str]:
        parts = re.split(r"(?<=[.!?])\s+|\n+", text)
        return [p.strip() for p in parts if len(re.sub(r"[^가-힣]", "", p)) >= 6]

    _, rows = collect_mod.load_dump(args.dump or collect_mod.dump_path(args.label))
    answers = {row["id"]: row.get("answer", "") for row in rows}
    left = {j.id: j for j in _load_judgments(_judgments_path(args.label))}
    right = {j.id: j for j in _load_judgments(_judgments_path(args.against))}

    total = 0
    for qid in sorted(left.keys() & right.keys()):
        shared = [
            s
            for s in sentences(answers.get(qid, ""))
            if covered(s, left[qid].supported) < 0.34
            and covered(s, right[qid].supported) < 0.34
        ]
        if not shared:
            continue
        print(f"\n── {qid}")
        for sentence in shared:
            print(f"   {sentence}")
        total += len(shared)

    print(f"\n둘 다 확인 목록에 안 올린 문장 {total}개.")
    print("   연결어·머리말이 대부분입니다 — 그건 채점 대상이 아니라 정상입니다.")
    print("   **내용이 있는 문장**이 여기 있으면 그것을 보세요. t03↔t19 가 그렇게 나왔습니다.")
    return 0


def cmd_agreement(args: argparse.Namespace) -> int:
    reference = _load_judgments(_judgments_path(args.against))
    candidate = _load_judgments(_judgments_path(args.label))
    ref_model, cand_model = _judge_model(args.against), _judge_model(args.label)
    result = judge_mod.agreement(reference, candidate)

    print(f"기준 {args.against} ({ref_model})  ↔  후보 {args.label} ({cand_model})")
    print(f"일치 {result['agreed']}/{result['n']}   "
          f"(분모는 **양쪽에 다 있는 문항**입니다 — RAG-075 ①)")
    for row in result["mismatch"]:
        print(f"\n── {row['id']}  기준={row['reference']} 후보={row['candidate']}")
        print(f"   기준 근거: {row['reference_why']}")
        print(f"   후보가 짚은 것: {row['candidate_unsupported']}")

    # **사람이 한쪽에 있으면 뜻이 다르다.** LLM 둘의 일치는 같은 맹점을 공유해도 높게 나오지만,
    # 사람과의 일치는 `RAG-007` 이 요구한 바로 그 수다. 다만 **승격은 포기했으므로**(D-060 ⑨)
    # 어느 쪽이든 이 수로 지표를 만들지 않는다 — 분모 30 은 이제 목표가 아니라 되열기 조건이다.
    if "human" in (ref_model, cand_model):
        print(f"\n사람과의 일치입니다 — `RAG-007` 이 요구한 종류의 수이고 분모는 {result['n']}개"
              " 입니다.")
        print("   ⚠ 그래도 지표로 만들지 않습니다. **승격은 포기했습니다** (D-060 ⑨).")
        print("   분모 30 은 이제 목표가 아니라 **카드를 다시 여는 조건**입니다 — 사람 라벨을")
        print("   30개까지 달겠다는 사람이 나오면 그때 이 수가 뜻을 갖습니다.")
    else:
        print("\n⚠ 이것은 **판정자 간 일치율**이지 캘리브레이션이 아닙니다 (D-060 ⑦).")
        print("   LLM 둘이 일치하는 것은 둘이 같은 맹점을 공유하는 것일 수도 있습니다.")
        print("   특히 검증자가 GPT 계열이면 judge 와 같은 계열이라, D-060 ① 이 일부러 갈라 둔")
        print("   self-preference 분리가 무너져 이 수가 부풀려집니다. 지표로 승격하지 마세요.")

    # 실측이 그것을 보였다 — 이 카드에서 결함을 찾은 것은 비율이 아니라 선별이었다.
    print("\n이 수보다 아래 둘을 보세요 (D-060 ⑨):")
    print(f"   → `review --label {args.label}`")
    print(f"   → `blindspots --label {args.label} --against {args.against}`")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="daengs_evals.training_quality", description=__doc__)
    parser.add_argument("--judge-model", default=None, help="기본은 settings.openai_judge_model")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check-anchors", help="judge 가 쓸 만한지 잰다 (score 의 전제)")

    p_score = sub.add_parser("score", help="덤프를 채점한다")
    p_score.add_argument("--label", required=True)
    p_score.add_argument("--dump", type=Path, default=None)
    p_score.add_argument("--limit", type=int, default=0)

    p_review = sub.add_parser("review", help="사람이 볼 문항을 고르고 읽을 한 장을 낸다")
    p_review.add_argument("--label", required=True)
    p_review.add_argument("--dump", type=Path, default=None)
    p_review.add_argument("--out", type=Path, default=None,
                          help="기본은 evals/training_quality/review_<label>.md")

    p_blind = sub.add_parser("blindspots", help="두 판정자가 공통으로 안 훑은 문장")
    p_blind.add_argument("--label", required=True)
    p_blind.add_argument("--against", required=True)
    p_blind.add_argument("--dump", type=Path, default=None)

    p_agree = sub.add_parser("agreement", help="두 판정 파일의 일치율")
    p_agree.add_argument("--label", required=True)
    p_agree.add_argument("--against", required=True, help="교차검증 상대 (보통 <label>__codex)")

    args = parser.parse_args(argv)
    handlers: dict[str, Any] = {
        "blindspots": cmd_blindspots,
        "check-anchors": cmd_check_anchors,
        "score": cmd_score,
        "review": cmd_review,
        "agreement": cmd_agreement,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
