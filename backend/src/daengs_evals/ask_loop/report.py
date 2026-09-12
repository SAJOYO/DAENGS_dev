"""되묻기 종료 시험 — 셈과 리포트. 모델 없음.

    uv run python -m daengs_evals.ask_loop.report --label al_v1

대본마다 넷을 본다.

| 이름 | 무엇 | 왜 |
| --- | --- | --- |
| `closed_on_time` | `must_close_by` 턴부터는 되묻기(CLARIFY)가 아니다 | 09-12 루프의 정의 그 자체 |
| `repeated_question` | 같은 되묻기 문장이 두 번 나갔다 (공백·문장부호 무시) | 실사용에서 글자까지 같은 질문이 셋 |
| `reasked_axis` | 앞 되묻기가 물은 항목(`missing_axes`)을 뒤 되묻기가 또 물었다 | v7 규칙 ⓑ |
| `first_ask_ok` | 첫 턴에 되묻는 게 맞는 대본에서 첫 턴이 CLARIFY 다 | 되묻기를 아예 안 하는 것도 실패다 (시험 ③ under_ask) |

결과 이름: `closed_on_time` · `late`(닫긴 했는데 늦게) · `never_closed` · `failed`(끊김).
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Any

from daengs_evals.answer_quality.provenance import utc_now
from daengs_evals.ask_loop.collect import ASSETS_DIR, STRATA, load_runs, runs_path
from daengs_evals.calibration.stats import wilson_interval

_WS = re.compile(r"[\s\.\?\!,~…]+")


def norm(text: str | None) -> str:
    return _WS.sub("", text or "")


def outcome_of(row: dict[str, Any]) -> dict[str, Any]:
    turns = row.get("turns") or []
    must = int(row["must_close_by"])
    if any(t.get("status") == "FAILED" for t in turns) or len(turns) == 0:
        result = "failed"
    else:
        asks = [i + 1 for i, t in enumerate(turns) if t.get("status") == "CLARIFY"]
        late_asks = [i for i in asks if i >= must]
        if not late_asks:
            result = "closed_on_time"
        elif late_asks[-1] < len(turns):
            result = "late"
        else:
            result = "never_closed"
    questions = [
        norm((t.get("clarify") or {}).get("question"))
        for t in turns
        if t.get("status") == "CLARIFY"
    ]
    repeated = len(questions) != len(set(questions))
    seen_axes: set[str] = set()
    reasked = False
    for t in turns:
        if t.get("status") != "CLARIFY":
            continue
        axes = set((t.get("clarify") or {}).get("missing_axes") or [])
        if axes & seen_axes:
            reasked = True
        seen_axes |= axes
    first_ok = None
    if row.get("first_should_ask"):
        first_ok = bool(turns) and turns[0].get("status") == "CLARIFY"
    return {
        "result": result,
        "asks": sum(1 for t in turns if t.get("status") == "CLARIFY"),
        "repeated_question": repeated,
        "reasked_axis": reasked,
        "first_ask_ok": first_ok,
    }


def summarize(meta: dict[str, Any], rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    results: Counter = Counter()
    by_stratum: dict[str, Counter] = defaultdict(Counter)
    flags = Counter()
    first = [0, 0]
    problems = []
    measured = 0
    for r in rows:
        o = outcome_of(r)
        results[o["result"]] += 1
        by_stratum[r["stratum"]][o["result"]] += 1
        if o["result"] == "failed":
            continue
        measured += 1
        if o["repeated_question"]:
            flags["repeated_question"] += 1
        if o["reasked_axis"]:
            flags["reasked_axis"] += 1
        if o["first_ask_ok"] is not None:
            first[1] += 1
            first[0] += int(o["first_ask_ok"])
        bad = o["result"] != "closed_on_time" or o["repeated_question"] or o["reasked_axis"]
        bad = bad or o["first_ask_ok"] is False
        if bad:
            problems.append(
                {
                    "script_id": r["script_id"],
                    "stratum": r["stratum"],
                    "must_close_by": r["must_close_by"],
                    **o,
                    "turns": [
                        {
                            "user": t.get("user"),
                            "status": t.get("status"),
                            "capabilities": t.get("capabilities"),
                            "relation": t.get("relation"),
                            "message": (t.get("message") or "")[:200],
                            "question": (t.get("clarify") or {}).get("question"),
                            "missing_axes": (t.get("clarify") or {}).get("missing_axes"),
                        }
                        for t in r.get("turns") or []
                    ],
                }
            )
    strata = {}
    for name in STRATA:
        c = by_stratum.get(name)
        if c:
            n = sum(v for k, v in c.items() if k != "failed")
            strata[name] = {
                "n": n,
                "closed_on_time": c.get("closed_on_time", 0),
                "rate": wilson_interval(c.get("closed_on_time", 0), n).as_dict() if n else None,
                "results": dict(c),
            }
    tokens = Counter()
    for r in rows:
        for t in r.get("turns") or []:
            for k, v in (t.get("tokens") or {}).items():
                tokens[f"{k}_in"] += v["in"]
                tokens[f"{k}_out"] += v["out"]
    return {
        "label": meta.get("label"),
        "general_prompt": (meta.get("general") or {}).get("prompt_version"),
        "n": len(rows),
        "measured": measured,
        "results": dict(results),
        "closed_on_time": wilson_interval(results.get("closed_on_time", 0), measured).as_dict()
        if measured
        else None,
        "strata": strata,
        "flags": dict(flags),
        "first_ask": {"ok": first[0], "n": first[1]},
        "problems": problems,
        "tokens": dict(tokens),
        "generated_at": utc_now(),
    }


def _pct(d: dict[str, Any] | None) -> str:
    return (
        "—" if not d else f"{d['point'] * 100:.0f}% [{d['low'] * 100:.0f}, {d['high'] * 100:.0f}]"
    )


def render_markdown(s: dict[str, Any]) -> str:
    res = s["results"]
    lines = [
        f"# 되묻기 종료 시험 `{s['label']}` — general `{s['general_prompt']}`",
        "",
        "**잠정** — 사람 라벨 없음. 되묻기는 status 로, 반복은 문자열·항목 비교로 센 것이라 판정기 편향은 없다.",
        "",
        f"- 대본 {s['n']} · 잰 것 {s['measured']} · 끊김 {res.get('failed', 0)}",
        (
            f"- 제때 닫힘 {res.get('closed_on_time', 0)} / {s['measured']} = {_pct(s['closed_on_time'])}"
            f" · 늦게 {res.get('late', 0)} · 끝까지 되물음 {res.get('never_closed', 0)}"
        ),
        f"- 같은 질문 반복 {s['flags'].get('repeated_question', 0)} · 물은 항목 또 물음 {s['flags'].get('reasked_axis', 0)}",
        f"- 첫 턴에 되물어야 하는 대본에서 실제로 되물음 {s['first_ask']['ok']} / {s['first_ask']['n']}",
        "",
        "| 층 | 잰 것 | 제때 닫힘 | 비율 [95% CI] | 늦게 | 끝까지 되물음 |",
        "| --- | ---: | ---: | --- | ---: | ---: |",
    ]
    for name, v in s["strata"].items():
        r = v["results"]
        lines.append(
            f"| {name} | {v['n']} | {v['closed_on_time']} | {_pct(v['rate'])} | {r.get('late', 0)} | {r.get('never_closed', 0)} |"
        )
    lines += ["", f"## 문제 대본 ({len(s['problems'])})", ""]
    for p in s["problems"]:
        why = [p["result"]] if p["result"] != "closed_on_time" else []
        if p["repeated_question"]:
            why.append("같은 질문 반복")
        if p["reasked_axis"]:
            why.append("물은 항목 또 물음")
        if p["first_ask_ok"] is False:
            why.append("첫 턴에 안 되물음")
        lines.append(
            f"### `{p['script_id']}` ({p['stratum']}, {p['must_close_by']}턴부터 닫혀야) — {', '.join(why)}"
        )
        lines.append("")
        for i, t in enumerate(p["turns"], 1):
            lines.append(f"- {i}. 보호자: {t['user']}")
            tail = (
                f" — 되묻기: {t['question']} {t['missing_axes'] or ''}"
                if t["status"] == "CLARIFY"
                else ""
            )
            rel = f" (관계 {t['relation']})" if t.get("relation") else ""
            lines.append(f"  - {t['status']} {t['capabilities'] or ''}{rel}: {t['message']}{tail}")
        lines.append("")
    lines += [
        "## 읽는 법",
        "",
        "- `closed_on_time` = 정해 둔 턴부터 되묻기가 아니다. 2026-09-12 실사용 루프(같은 질문 셋)를 이 대본 모양으로 잡는다.",
        "- 이 시험은 **답이 맞는지는 안 본다.** 닫혔다는 것은 status 가 CLARIFY 가 아니라는 뜻이고, 그 답의 품질은 시험 ③ 판정기 몫이다.",
        "- 첫 턴에 되물어야 하는 층에서 안 되물으면 그것도 적는다 — 되묻기를 없애는 방향으로 고치면 이 줄이 먼저 나빠진다.",
        "- 앞 대화는 서버와 같은 규칙으로 넘긴다(완료 턴 전부 + 마지막이 CLARIFY 일 때만 대기). DB 는 안 쓴다.",
        "",
    ]
    return "\n".join(lines)


def run(label: str) -> dict[str, Any]:
    meta, rows = load_runs(runs_path(label))
    s = summarize(meta, rows)
    (ASSETS_DIR / f"report_{label}.md").write_text(render_markdown(s), encoding="utf-8")
    (ASSETS_DIR / f"summary_{label}.json").write_text(
        json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return s


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="되묻기 종료 시험 리포트")
    parser.add_argument("--label", required=True)
    args = parser.parse_args(argv)
    s = run(args.label)
    print(
        f"제때 닫힘 {_pct(s['closed_on_time'])} · 문제 대본 {len(s['problems'])} → {ASSETS_DIR / f'report_{args.label}.md'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
