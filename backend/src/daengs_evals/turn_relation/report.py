"""관계 혼동행렬 — 판정기 없이 코드로.

    uv run python -m daengs_evals.turn_relation.report --label tr_v1

`got` 은 리졸버가 낸 관계 그대로이되, 서비스가 실제로 **쓰는** 값으로 접는다:
    확신 < 0.6            → LOW_CONFIDENCE  (서비스가 버리고 라우터로 감 — 이어짐이 안 됨)
    모델을 안 태우고 NEW  → FAST_NEW        (표지어 정규식 빠른 길)
    스키마·제공자 실패    → ERROR           (서비스는 이력 없던 때처럼 답함)
그래서 "맞음" 은 `expect == got` 이고, NEW 기대에 FAST_NEW 가 오면 맞은 것으로 센다 — 빠른 길이
바로 그러라고 있는 것이다.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Any

from daengs_evals.answer_quality.provenance import utc_now
from daengs_evals.calibration.stats import wilson_interval
from daengs_evals.turn_relation.collect import ASSETS_DIR, STRATA, load_runs, runs_path

GOT_ORDER: tuple[str, ...] = (
    "NEW",
    "FAST_NEW",
    "FOLLOW_UP",
    "CORRECTION",
    "REPEAT",
    "META",
    "LOW_CONFIDENCE",
    "ERROR",
    "RUNNER_ERROR",
)


def got_of(row: dict[str, Any], *, floor: float) -> str:
    if row.get("runner_error"):
        return "RUNNER_ERROR"
    o = row["observed"]
    if o.get("error"):
        return "ERROR"
    rel = o.get("relation")
    if rel is None:
        return "ERROR"
    if rel == "NEW" and not o.get("model_called"):
        return "FAST_NEW"
    conf = o.get("confidence")
    if rel != "NEW" and conf is not None and conf < floor:
        return "LOW_CONFIDENCE"
    return rel


def is_correct(expect: str, got: str) -> bool:
    if expect == "NEW":
        return got in ("NEW", "FAST_NEW")
    return expect == got


def summarize(meta: dict[str, Any], rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    floor = float(meta.get("resolver", {}).get("confidence_floor", 0.6))
    confusion: dict[str, Counter] = defaultdict(Counter)
    by_stratum: dict[str, list[bool]] = defaultdict(list)
    misses = []
    for r in rows:
        got = got_of(r, floor=floor)
        ok = is_correct(r["expect"], got)
        confusion[r["expect"]][got] += 1
        by_stratum[r["stratum"]].append(ok)
        if not ok:
            o = r["observed"]
            misses.append(
                {
                    "script_id": r["script_id"],
                    "stratum": r["stratum"],
                    "current": r["current"],
                    "expect": r["expect"],
                    "got": got,
                    "confidence": o.get("confidence"),
                    "standalone_query": o.get("standalone_query"),
                    "error": o.get("error"),
                    "status": r.get("status"),
                }
            )
    n_ok = sum(sum(v) for v in by_stratum.values())
    strata = {}
    for name in STRATA:
        v = by_stratum.get(name)
        if v:
            strata[name] = {
                "n": len(v),
                "correct": sum(v),
                "rate": wilson_interval(sum(v), len(v)).as_dict(),
            }
    tokens = Counter()
    for r in rows:
        for k, t in r.get("tokens", {}).items():
            tokens[f"{k}_in"] += t["in"]
            tokens[f"{k}_out"] += t["out"]
    used_but_not_resolved_version = sum(
        1
        for r in rows
        if got_of(r, floor=floor) in ("FOLLOW_UP", "CORRECTION", "REPEAT")
        and r.get("router_prompt_version")
        and not str(r["router_prompt_version"]).endswith("-resolved")
    )
    return {
        "label": meta.get("label"),
        "n": len(rows),
        "accuracy": wilson_interval(n_ok, len(rows)).as_dict() if rows else None,
        "strata": strata,
        "confusion": {e: dict(c) for e, c in confusion.items()},
        "misses": misses,
        "model_called": sum(1 for r in rows if r["observed"].get("model_called")),
        "wiring_mismatch": used_but_not_resolved_version,
        "tokens": dict(tokens),
        "resolver": meta.get("resolver"),
        "generated_at": utc_now(),
    }


def _pct(d: dict[str, Any] | None) -> str:
    return (
        "—" if not d else f"{d['point'] * 100:.0f}% [{d['low'] * 100:.0f}, {d['high'] * 100:.0f}]"
    )


def render_markdown(s: dict[str, Any]) -> str:
    r = s.get("resolver") or {}
    lines = [
        f"# 앞 대화 기억 — `{s['label']}`",
        "",
        f"리졸버 {r.get('model')} · {r.get('prompt_version')} · 확신 바닥 {r.get('confidence_floor')} · 판정기 없음 · {s['generated_at']}",
        "",
        f"대본 {s['n']} · 맞음 {_pct(s['accuracy'])} · 리졸버 모델 호출 {s['model_called']}/{s['n']}",
        "",
        "## 층별",
        "",
        "| 층 | 맞음 / n | 비율 [95% CI] |",
        "| --- | --- | --- |",
    ]
    for name, d in s["strata"].items():
        lines.append(f"| {name} | {d['correct']} / {d['n']} | {_pct(d['rate'])} |")
    gots = [g for g in GOT_ORDER if any(g in c for c in s["confusion"].values())]
    lines += [
        "",
        "## 혼동행렬 (행 = 기대, 열 = 리졸버가 낸 것)",
        "",
        "| 기대 \\ 결과 | " + " | ".join(gots) + " |",
        "| --- |" + " --- |" * len(gots),
    ]
    for e in ("NEW", "FOLLOW_UP", "CORRECTION", "REPEAT", "META"):
        c = s["confusion"].get(e)
        if c:
            lines.append(f"| **{e}** | " + " | ".join(str(c.get(g, "")) for g in gots) + " |")
    lines += ["", f"## 틀린 것 ({len(s['misses'])})", ""]
    for m in s["misses"]:
        conf = f" (확신 {m['confidence']:.2f})" if m.get("confidence") is not None else ""
        err = f" — {m['error']['kind']}: {m['error']['detail'][:80]}" if m.get("error") else ""
        lines.append(
            f'- `{m["script_id"]}` [{m["stratum"]}] "{m["current"]}" 기대 {m["expect"]} → **{m["got"]}**{conf}{err}'
        )
    lines += [
        "",
        "## 배선 확인",
        "",
        f"- 관계를 썼는데 라우터 프롬프트가 `-resolved` 가 아닌 대본: **{s['wiring_mismatch']}** (0 이어야 한다)",
        f"- 토큰: resolver {s['tokens'].get('resolver_in', 0)}/{s['tokens'].get('resolver_out', 0)} · router {s['tokens'].get('router_in', 0)}/{s['tokens'].get('router_out', 0)} · general {s['tokens'].get('general_in', 0)}/{s['tokens'].get('general_out', 0)}",
        "",
        "## 읽는 법",
        "",
        "- `FAST_NEW` 는 표지어 정규식이 모델을 안 태운 것. `followup_no_marker` 층에서 이게 나오면 이어짐을 **구조적으로** 놓친 것이다.",
        "- `LOW_CONFIDENCE` 는 관계는 맞혔을 수 있지만 서비스가 버린 것. 틀린 것과 따로 센다.",
        "- `META` 기대는 리졸버 프롬프트에는 있고 열거형에는 없는 값이다 — 어디로 접히는지가 결과다.",
        "",
    ]
    return "\n".join(lines)


def run(label: str) -> dict[str, Any]:
    meta, rows = load_runs(runs_path(label))
    s = summarize(meta, rows)
    (ASSETS_DIR / f"summary_{label}.json").write_text(
        json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (ASSETS_DIR / f"report_{label}.md").write_text(render_markdown(s), encoding="utf-8")
    return s


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="앞 대화 기억 리포트")
    parser.add_argument("--label", required=True)
    args = parser.parse_args(argv)
    s = run(args.label)
    for name, d in s["strata"].items():
        print(f"  {name:20s} {d['correct']:3d}/{d['n']:<3d} {_pct(d['rate'])}")
    print(
        f"맞음 {_pct(s['accuracy'])} · 틀림 {len(s['misses'])} → {ASSETS_DIR / f'report_{args.label}.md'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
