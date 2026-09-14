"""라우터가 고른 담당 vs 기대 — 이미 모은 셀에서 공짜로 뽑는 표.

    uv run python -m daengs_evals.route_check

시험 ①·②·③ 의 질문은 전부 **general 이 답하라고 만든 돌봄 질문**이고, Life 묶음은 Life RAG 를 재려고 만든
질문이다. 그래서 기대 담당은 질문 파일이 정한다 — 응급 어휘가 든 질문(expectations 의 `defer`+`emergency`)만
`vet_contact` 가 맞다. 셀에는 라우터가 실제로 고른 담당(`capabilities`)이 적혀 있으니 둘을 나란히 놓으면 된다.
모델 호출 0.

**불일치가 곧 틀림은 아니다.** "여름 산책" 이 `walk+general` 로 간 것은 산책 판정 담당이 같이 도는 것이라
변명이 되고, "놀이" 가 `training` 으로 간 것도 그렇다. 반대로 "컨디션 어때" 가 보행(gait) 넘김으로 간 것과
"초콜릿이 왜 위험해요" 가 응급 사전에 걸린 것은 시험 ③ 이 이미 과잉 거절로 센 것이다. 표는 사람이 가를
목록이지 점수가 아니다.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from daengs_evals.answer_quality.provenance import utc_now
from daengs_evals.calibration.stats import wilson_interval
from daengs_evals.profile_fitness.collect import cells_path, load_cells
from daengs_evals.profile_fitness.profiles import ASSETS_DIR

DEFERRAL_DIR = ASSETS_DIR.parent / "deferral"

#: 셀 묶음 → 그 질문들을 만들 때 답하라고 정한 담당.
DEFAULT_EXPECT: dict[str, str] = {
    "pf_ask_v1": "general",
    "pf_v6_sub18": "general",
    "pf_carelog_v1": "general",
    "pf_life_v1": "life",
}


def load_expectations(label: str) -> dict[str, dict[str, Any]]:
    p = DEFERRAL_DIR / f"expectations_{label}.jsonl"
    if not p.exists():
        return {}
    out = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out[row["question_id"]] = row
    return out


def expected_of(label: str, question_id: str, expectations: dict[str, dict[str, Any]]) -> str:
    e = expectations.get(question_id)
    if e and e.get("expect") == "defer" and e.get("expected_reason") == "emergency":
        return "vet_contact"
    return DEFAULT_EXPECT[label]


def got_of(cell: dict[str, Any]) -> str:
    caps = list(cell.get("capabilities") or [])
    return "+".join(caps) if caps else "(없음)"


def _queries(meta: dict[str, Any]) -> dict[str, str]:
    raw = meta.get("questions_path")
    if not raw:
        return {}
    p = Path(raw)
    if not p.is_absolute():
        p = ASSETS_DIR.parents[1] / p
    if not p.exists():
        return {}
    out = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            q = json.loads(line)
            out[q["question_id"]] = q["query"]
    return out


def check(label: str) -> dict[str, Any]:
    meta, cells = load_cells(cells_path(label))
    expectations = load_expectations(label)
    queries = _queries(meta)
    n = ok = 0
    mismatch: Counter = Counter()
    example: dict[tuple[str, str, str], dict[str, Any]] = {}
    for c in cells:
        if c.get("status") == "FAILED":
            continue
        n += 1
        exp = expected_of(label, c["question_id"], expectations)
        got = got_of(c)
        if got == exp:
            ok += 1
            continue
        key = (c["question_id"], exp, got)
        mismatch[key] += 1
        example.setdefault(
            key,
            {
                "question_id": c["question_id"],
                "query": queries.get(c["question_id"], "?"),
                "expected": exp,
                "got": got,
                "status": c.get("status"),
            },
        )
    rows = [{**example[k], "cells": v} for k, v in sorted(mismatch.items())]
    return {
        "label": label,
        "router": meta.get("router"),
        "n": n,
        "match": ok,
        "rate": wilson_interval(ok, n).as_dict() if n else None,
        "mismatches": rows,
    }


def _pct(d: dict[str, Any] | None) -> str:
    return (
        "—" if not d else f"{d['point'] * 100:.0f}% [{d['low'] * 100:.0f}, {d['high'] * 100:.0f}]"
    )


def render(results: list[dict[str, Any]]) -> str:
    lines = [
        "# 라우터가 고른 담당 vs 기대 (우리 셀, 모델 0회)",
        "",
        f"생성 {utc_now()}",
        "",
        "| 셀 묶음 | 라우터 | 셀 | 일치 | 일치율 [95% CI] |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for r in results:
        rt = r.get("router") or {}
        lines.append(
            f"| {r['label']} | {rt.get('prompt_version', '?')} | {r['n']} | {r['match']} | {_pct(r['rate'])} |"
        )
    total_n = sum(r["n"] for r in results)
    total_ok = sum(r["match"] for r in results)
    lines += [
        f"| **전체** | | {total_n} | {total_ok} | {_pct(wilson_interval(total_ok, total_n).as_dict())} |",
        "",
        "## 불일치 (질문 단위)",
        "",
        "| 셀 묶음 | 질문 | 기대 | 라우터가 고른 것 | 셀 | 상태 |",
        "| --- | --- | --- | --- | ---: | --- |",
    ]
    for r in results:
        for m in r["mismatches"]:
            lines.append(
                f"| {r['label']} | {m['query']} | {m['expected']} | {m['got']} | {m['cells']} | {m['status']} |"
            )
    lines += [
        "",
        "## 읽는 법",
        "",
        "- 기대 담당은 **질문을 만들 때 정한 것**이다 (돌봄 질문 → general, Life 질문 → life, 응급 어휘 질문 → vet_contact).",
        "  라우터 골드(`evals/orchestration_router/gold_v1.jsonl`, 80건)와는 다른 질문이라 그쪽 수치와 합치지 않는다.",
        "- 불일치가 곧 틀림은 아니다. `walk+general`·`training` 은 그 담당이 같이 도는 것이라 답이 나왔고, 시험 ①은 그 답을 그대로 판정했다.",
        "  `handoff:gait`(컨디션·저는 것 같아요) 와 `vet_contact`(초콜릿이 왜 위험해요) 는 시험 ③ 이 과잉 거절로 센 바로 그것이다.",
        "- 그래서 이 표는 점수가 아니라 **사람이 가를 목록**이다. 틀린 것으로 정해지면 라우터 골드에 그 질문을 더하는 것이 다음 일이다.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="라우터가 고른 담당 vs 기대")
    parser.add_argument("--labels", nargs="*", default=list(DEFAULT_EXPECT))
    args = parser.parse_args(argv)
    results = [check(lbl) for lbl in args.labels if cells_path(lbl).exists()]
    out_md = ASSETS_DIR / "report_route_check.md"
    out_md.write_text(render(results), encoding="utf-8")
    (ASSETS_DIR / "route_check.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for r in results:
        print(f"{r['label']:14s} {r['match']}/{r['n']}  불일치 질문 {len(r['mismatches'])}")
    print("→", out_md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
