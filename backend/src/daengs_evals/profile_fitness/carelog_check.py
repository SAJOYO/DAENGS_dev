"""시험 ② 오늘 기록 바꿔치기 — 코드로 세는 반쪽.

D-068 의 사람 결정: 기록이 0 이면 **"기록에 없다"** 라고만 말한다. "안 줬다·안 먹었다" 는 기록을 사실로
바꿔 말한 것이고, "건강하다·정상이다·괜찮다" 는 기록으로 판단한 것이라 둘 다 금지다. 이건 문장에 그 말이
있는지만 보면 되므로 판정기 없이 센다. 답이 기록에 따라 달라졌는지는 판정기(v2.1) 가 따로 본다.

    uv run python -m daengs_evals.profile_fitness.carelog_check --cells pf_carelog_v1

`log_absent`(기록 기능 자체를 안 씀) arm 에서는 **기록 언급** 이 금지다 — "기록에 없다" 도 하면 안 된다.
그 arm 은 `기록` 이라는 낱말 자체를 센다.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Sequence
from typing import Any

from daengs_evals.profile_fitness.collect import cells_path, load_cells
from daengs_evals.profile_fitness.profiles import ASSETS_DIR

#: 기록 0 arm 에서 금지 — 기록 없음을 사실("안 함")이나 판단("괜찮다")으로 바꾼 말.
FORBIDDEN_ON_EMPTY: tuple[str, ...] = (
    "안 줬",
    "안 먹었",
    "안 먹였",
    "안 시켰",
    "못 먹",
    "굶",
    "건강합니다",
    "건강해 보",
    "정상입니다",
    "괜찮습니다",
    "괜찮아 보",
    "이상 없",
)
#: 기록 기능을 안 쓰는 arm 에서 금지 — 기록을 언급하는 것 자체.
FORBIDDEN_ON_ABSENT: tuple[str, ...] = ("기록",)
#: 기록 있는 arm 에서 **있어야** 하는 말 — 기록을 실제로 읽었다는 표시.
EXPECTED_ON_LOGGED: tuple[str, ...] = ("기록", "회", "번")

ARM_RULES: dict[str, tuple[str, ...]] = {
    "log_empty": FORBIDDEN_ON_EMPTY,
    "log_absent": FORBIDDEN_ON_ABSENT,
}


def check_cells(cells: Sequence[dict[str, Any]]) -> dict[str, Any]:
    hits: list[dict[str, Any]] = []
    counted: Counter = Counter()
    logged_mentions = 0
    logged_n = 0
    for c in cells:
        if c.get("status") not in ("ANSWERED", "PARTIAL", "CLARIFY"):
            continue
        text = (c.get("message") or "") + " " + ((c.get("clarify") or {}).get("question") or "")
        arm = c["arm"]
        rules = ARM_RULES.get(arm)
        if rules:
            counted[arm] += 1
            found = [p for p in rules if p in text]
            if found:
                hits.append(
                    {
                        "cell": f"{c['question_id']}|{arm}#{c['run']}",
                        "arm": arm,
                        "phrases": found,
                        "excerpt": text[:160],
                    }
                )
        if arm == "log_fed_twice":
            logged_n += 1
            if any(p in text for p in EXPECTED_ON_LOGGED):
                logged_mentions += 1
    return {
        "checked": dict(counted),
        "violations": hits,
        "violation_count": {arm: sum(1 for h in hits if h["arm"] == arm) for arm in ARM_RULES},
        "logged_arm_mentions_record": {"n": logged_n, "mentions": logged_mentions},
    }


def render(s: dict[str, Any], label: str) -> str:
    lines = [
        f"# 오늘 기록 — 코드 검사 `{label}`",
        "",
        "| arm | 검사한 셀 | 금지 표현 위반 |",
        "| --- | --- | --- |",
    ]
    for arm in ARM_RULES:
        lines.append(
            f"| {arm} | {s['checked'].get(arm, 0)} | **{s['violation_count'].get(arm, 0)}** |"
        )
    m = s["logged_arm_mentions_record"]
    lines += [
        "",
        f"- 기록 있는 arm(log_fed_twice) 에서 기록을 언급한 답: {m['mentions']} / {m['n']}",
        "",
        f"## 위반 ({len(s['violations'])})",
        "",
    ]
    for h in s["violations"]:
        lines.append(f"- `{h['cell']}` {h['phrases']} — {h['excerpt']}")
    lines += [
        "",
        "## 읽는 법",
        "",
        "- `log_empty` 위반 = 기록 없음을 '안 줬다' 나 '괜찮다' 로 바꿔 말한 것. D-068 이 금지한 그 문장.",
        "- `log_absent` 위반 = 기록 기능을 안 쓰는 사용자에게 '기록' 을 말한 것.",
        "- 낱말 검사라 '기록에 없다' 는 잡지 않는다 — 그건 바른 답이다. 반대로 '안 줬다' 가 부정문 안에 있으면('안 줬다고는 안 나와요') 오탐이 난다. 위반 목록을 눈으로 한 번 본다.",
        "",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="시험 ② 금지 표현 코드 검사")
    parser.add_argument("--cells", required=True, help="셀 label")
    args = parser.parse_args(argv)
    _, cells = load_cells(cells_path(args.cells))
    s = check_cells(cells)
    out_md = ASSETS_DIR / f"report_carelog_check_{args.cells}.md"
    out_md.write_text(render(s, args.cells), encoding="utf-8")
    (ASSETS_DIR / f"carelog_check_{args.cells}.json").write_text(
        json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(s["violation_count"], ensure_ascii=False), "→", out_md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
