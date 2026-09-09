"""두 수집의 레코드별 기계 대조 — 같은 질문 파일로 돈 두 랩이 **어느 칸에서** 다른가 (#330).

요약 표나 `cited_diff` 는 인용만 본다. 이 도구는 문항마다 **라우팅 계획 · 선택 능력 · 최상위 상태 ·
Life 상태/코드/본문/인용 · Walk/Place 상태** 를 칸별로 맞대고, 다른 칸의 수와 문항 id 를 적는다.
"인용 말고는 1글자도 안 달라졌다" 는 주장을 사람이 표를 읽어 믿는 대신 여기서 센다.

축 둘을 갈라 읽는다.

- **라우팅 축** (`plan` · 선택 능력 · 최상위 상태) — 라우터 프롬프트가 바뀌면 여기가 움직인다.
- **Life 축** (Life status · refusal/abstention code · 본문 · citations 라벨) — 생성·검색·코퍼스가
  바뀌면 여기가 움직인다. Life 는 다른 능력이 무엇을 하든 같은 질문과 같은 판정 컨텍스트를 받으므로
  라우팅 축이 움직여도 Life 축은 그대로여야 한다. `--assert-life` 는 그 성질을 exit code 로 낸다.

`plan` 의 `model` · `prompt_version` · `router` 는 뺀다 — 그것이 다른 것은 이미 meta 행이 말한다.
Walk 는 실시간 날씨라 원래 안 맞고(#318 함정 3), 보고만 한다.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

VOLATILE_PLAN = frozenset({"model", "prompt_version", "router"})
DEFAULT_DIR = Path("evals/answer_quality")


def load_answers(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            if row.get("kind") == "answer":
                rows[str(row["question_id"])] = row
    return rows


def _life(row: Mapping[str, Any]) -> Mapping[str, Any] | None:
    return next((r for r in row.get("results") or [] if r.get("capability") == "life"), None)


def _life_text(result: Mapping[str, Any] | None) -> str | None:
    if not result:
        return None
    return (
        (result.get("data") or {}).get("answer")
        or (result.get("refusal") or {}).get("message")
        or (result.get("abstention") or {}).get("message")
    )


def _life_code(result: Mapping[str, Any] | None) -> str | None:
    if not result:
        return None
    return ((result.get("refusal") or result.get("abstention") or {}).get("code"))


def _labels(result: Mapping[str, Any] | None) -> list[str | None]:
    return [c.get("label") for c in ((result or {}).get("data") or {}).get("citations") or []]


def _plan_key(row: Mapping[str, Any]) -> str:
    plan = row.get("plan") or {}
    if isinstance(plan, dict):
        plan = {k: v for k, v in plan.items() if k not in VOLATILE_PLAN}
    return json.dumps(plan, sort_keys=True, ensure_ascii=False)


def _capabilities(row: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(r["capability"] for r in row.get("results") or []))


def _status_of(row: Mapping[str, Any], capability: str) -> str | None:
    return next((r["status"] for r in row.get("results") or [] if r["capability"] == capability), None)


FIELDS = ("plan", "capabilities", "top_status", "life_status", "life_code",
          "life_text", "life_text_normalized", "life_citations", "refusal_evidence",
          "walk_status", "place_status")
ROUTING_FIELDS = ("plan", "capabilities", "top_status")
LIFE_FIELDS = ("life_status", "life_code", "life_text", "life_citations")


def diff(a: Mapping[str, dict[str, Any]], b: Mapping[str, dict[str, Any]],
         *, exclude_styles: tuple[str, ...] = ()) -> dict[str, Any]:
    """문항마다 칸별로 맞댄다. 두 수집의 문항 집합이 다르면 그것부터 적는다."""
    only_a, only_b = sorted(set(a) - set(b)), sorted(set(b) - set(a))
    counts: Counter[str] = Counter()
    where: dict[str, list[str]] = defaultdict(list)
    compared = 0
    for qid in sorted(set(a) & set(b)):
        ra, rb = a[qid], b[qid]
        style = str(ra.get("stratum", "")).split("__")[-1]
        if style in exclude_styles:
            continue
        compared += 1
        la, lb = _life(ra), _life(rb)
        ta, tb = _life_text(la), _life_text(lb)
        checks = {
            "plan": _plan_key(ra) == _plan_key(rb),
            "capabilities": _capabilities(ra) == _capabilities(rb),
            "top_status": ra.get("status") == rb.get("status"),
            "life_status": (la or {}).get("status") == (lb or {}).get("status"),
            "life_code": _life_code(la) == _life_code(lb),
            "life_text": ta == tb,
            "life_text_normalized": " ".join((ta or "").split()) == " ".join((tb or "").split()),
            # OK 답변의 인용 라벨. **거절의 인용은 여기서 안 센다** — RAG-077 이전 수집은 거절에
            # `data` 가 없어 그 칸은 설계상 다르다. 그 차이는 `refusal_evidence` 로 따로 보고한다
            "life_citations": (
                (la or {}).get("status") != "OK" or (lb or {}).get("status") != "OK"
                or _labels(la) == _labels(lb)
            ),
            "refusal_evidence": (
                (la or {}).get("status") != "REFUSED" or (lb or {}).get("status") != "REFUSED"
                or bool(_labels(la)) == bool(_labels(lb))
            ),
            "walk_status": _status_of(ra, "walk") == _status_of(rb, "walk"),
            "place_status": _status_of(ra, "place") == _status_of(rb, "place"),
        }
        for field, same in checks.items():
            if not same:
                counts[field] += 1
                where[field].append(qid)
    return {
        "compared": compared,
        "only_a": only_a,
        "only_b": only_b,
        "mismatches": {f: counts.get(f, 0) for f in FIELDS},
        "where": {f: where.get(f, []) for f in FIELDS},
        "routing_moved": sum(counts.get(f, 0) for f in ROUTING_FIELDS),
        "life_moved": sum(counts.get(f, 0) for f in LIFE_FIELDS),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--a", required=True, help="라벨 (answers_<label>.jsonl)")
    parser.add_argument("--b", required=True)
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--exclude-style", action="append", default=[],
                        help="비교에서 뺄 문체 (예: multi_intent). 여러 번 줄 수 있다")
    parser.add_argument("--assert-life", action="store_true",
                        help="Life 축(상태·코드·본문·인용)이 하나라도 다르면 exit 1")
    parser.add_argument("--json", action="store_true", help="표 대신 JSON")
    args = parser.parse_args(argv)

    result = diff(
        load_answers(args.dir / f"answers_{args.a}.jsonl"),
        load_answers(args.dir / f"answers_{args.b}.jsonl"),
        exclude_styles=tuple(args.exclude_style),
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{args.a} ↔ {args.b}: 비교 {result['compared']}문항"
              + (f" · 한쪽에만 {result['only_a'] + result['only_b']}" if result["only_a"] or result["only_b"] else ""))
        print("| 칸 | 다른 문항 수 | 문항 |")
        print("| --- | --- | --- |")
        for field in FIELDS:
            ids = ", ".join(q.split("__")[-1] for q in result["where"][field])
            print(f"| {field} | {result['mismatches'][field]} | {ids or '—'} |")
        print(f"라우팅 축 움직임 {result['routing_moved']} · Life 축 움직임 {result['life_moved']}")
    if args.assert_life and result["life_moved"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
