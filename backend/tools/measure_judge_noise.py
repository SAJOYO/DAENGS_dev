"""판정기의 잡음 바닥을 잰다 — 같은 앵커를 N 번 채점해 얼마나 흔들리는지 (#401).

    uv run python tools/measure_judge_noise.py --runs 5 --anchor-set dev

**왜 이걸 재는가.** 이 카드의 위생 규칙은 *"채점자가 비결정적이면 같은 답변이 랩마다 다른
점수를 받아 랩 대조가 통째로 무의미해진다"* 이고, 그래서 `temperature=0` 을 걸었다. 그런데
첫 실전 앵커 실행 세 번이 9/12 · 8/12 · 9/12 로 갈렸다 — 앵커도 프롬프트도 `PROMPT_VERSION`
도 그대로인데. `temperature=0` 이 결정성을 주지 못한다는 뜻이다.

전후 비교를 하려면 **차이를 잡음과 견줄 수 있어야** 한다. before 0.8 → after 1.4 가
개선인지 흔들림인지 가르려면 흔들림의 크기를 먼저 알아야 하고, 이 스크립트가 그 수를 낸다.

**게이트와 같은 코드 경로로 잰다.** `anchors.check` 를 그대로 N 번 부른다 — 따로 만든
루프로 재면 게이트가 실제로 겪는 흔들림이 아니라 그 루프의 흔들림을 재게 된다.

일회성 측정 도구다. 결과를 보고 설계를 정하면 그 설계가 패키지로 들어가고 이 파일은 남을
이유가 없다 (`backend/tools/` 규약: 단일 파일, 패키지로 만들지 않는다).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from daengs_evals.conversation_quality import ASSETS_DIR
from daengs_evals.conversation_quality import anchors as anchors_mod
from daengs_evals.conversation_quality.judge import judge_model, openai_generate


def main(argv: list[str] | None = None) -> int:
    # Windows 콘솔의 기본 코드페이지(cp949)가 한글·기호를 못 실어 죽는 것을 막는다.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--anchor-set", choices=("dev", "holdout"), default="dev")
    parser.add_argument("--judge-model")
    parser.add_argument("--out")
    args = parser.parse_args(argv)

    model = args.judge_model or judge_model()
    runs: list[dict[str, int]] = []
    passed_counts: list[int] = []

    for i in range(args.runs):
        record = anchors_mod.check(args.anchor_set, generate=openai_generate, model=model)
        scores = {row["anchor_id"]: row["actual"] for row in record["results"]}
        runs.append(scores)
        passed_counts.append(record["n_passed"])
        print(f"  run {i + 1}/{args.runs}: {record['n_passed']}/{record['n']} 통과")

    anchor_ids = [a.anchor_id for a in anchors_mod.ANCHORS[args.anchor_set]]
    expected = {a.anchor_id: a.expected for a in anchors_mod.ANCHORS[args.anchor_set]}
    axis = {a.anchor_id: a.axis for a in anchors_mod.ANCHORS[args.anchor_set]}

    per_anchor = []
    unstable = 0
    for aid in anchor_ids:
        series = [r[aid] for r in runs]
        distinct = sorted(set(series))
        flips = len(distinct) > 1
        unstable += int(flips)
        per_anchor.append(
            {
                "anchor_id": aid,
                "axis": axis[aid],
                "expected": expected[aid],
                "scores": series,
                "distinct": distinct,
                "unstable": flips,
                # 기대와 **항상** 어긋나면 그것은 잡음이 아니라 앵커나 프롬프트의 문제다.
                "always_wrong": all(s != expected[aid] for s in series),
                "always_right": all(s == expected[aid] for s in series),
            }
        )

    summary = {
        "type": "judge_noise",
        "measured_at": datetime.now(UTC).isoformat(),
        "judge_model": model,
        "anchor_set": args.anchor_set,
        "anchors_sha256": anchors_mod.anchors_sha256(),
        "runs": args.runs,
        "n_anchors": len(anchor_ids),
        "passed_per_run": passed_counts,
        "gate_verdict_per_run": [c == len(anchor_ids) for c in passed_counts],
        "unstable_anchors": unstable,
        "unstable_ratio": round(unstable / len(anchor_ids), 4),
        "always_wrong": [r["anchor_id"] for r in per_anchor if r["always_wrong"]],
        "per_anchor": per_anchor,
    }

    out = Path(args.out) if args.out else ASSETS_DIR / f"judge_noise_{args.anchor_set}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print(f"실행마다 통과 수: {passed_counts}  (같은 입력·같은 temperature=0)")
    spread = max(passed_counts) - min(passed_counts)
    print(f"통과 수의 폭: {spread}")
    print(f"흔들린 앵커: {unstable}/{len(anchor_ids)} ({summary['unstable_ratio']:.0%})")
    if len(passed_counts) > 1:
        print(f"통과 수 표준편차: {statistics.stdev(passed_counts):.2f}")
    print()
    for row in per_anchor:
        if row["unstable"]:
            counts = Counter(row["scores"])
            spread_txt = " ".join(f"{s}×{n}" for s, n in sorted(counts.items()))
            print(f"  [흔들림] {row['anchor_id']:<32} 기대={row['expected']}  {spread_txt}")
    for row in per_anchor:
        if row["always_wrong"]:
            print(
                f"  [고정 불일치] {row['anchor_id']:<28} "
                f"기대={row['expected']} 실제={row['distinct']}"
            )
    print(f"\n→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
