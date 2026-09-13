"""응급 어휘 게이트 시험 — 모델 0회.

dev 의 `orchestration/emergency.py` 는 응급 발화를 **문자열 부분일치**로 잡고, 걸리면 모델을 안 태우고
`vet_contact` 하나로 끝낸다(D-065 · 응급이 라우터보다 앞). 그러니 "초콜릿이 왜 위험해요?" 처럼 응급
단어가 든 예방 질문은 라우터에 닿기도 전에 응급 처리된다 — pf_v1 축 B 에서 과잉거절로 잡혔던 그
문장이 이제는 **코드 레벨 오탐**이다.

`is_emergency(query)` 는 순수 함수라 문장 목록만 있으면 **돈 한 푼 없이** 정밀도·재현율이 나온다.
여기가 이 시험이 판정기보다 먼저인 이유다: 판정기가 볼 답은 게이트를 통과한 것뿐이라, 게이트가
틀리면 그 뒤의 어떤 점수도 그 문장을 못 본다.

    uv run python -m daengs_evals.deferral.emergency_gate --sentences evals/deferral/emergency_gate_v1.jsonl

문장은 다섯 층이다 (`stratum`):
    emergency             응급 (사전 단어 있음)             → True 여야 한다
    emergency_paraphrase  응급인데 사전 단어를 안 씀        → True 여야 하는데 못 잡을 것 — 재현율의 구멍
    symptom_nonurgent     증상이지만 응급 아님              → False 여야 한다 (되묻기·답 자리)
    trap_keyword          응급 단어가 들었지만 응급 아님    → False 여야 하는데 잡힐 것 — 정밀도의 구멍
    daily                 일상                              → False

층별로 갈라 보고하는 이유: 전체 정밀도 한 숫자는 층의 비율에 따라 얼마든지 움직인다. 발표에서 쓸 숫자는
"응급 30문장 중 30 잡음" 과 "함정 30문장 중 N 오탐" 두 줄이지 합친 것이 아니다.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from daengs_backend.orchestration.emergency import (
    AMBIGUOUS_TERMS,
    HIGH_TERMS,
    URGENCY_TERMS,
    is_emergency,
)
from daengs_evals.answer_quality.provenance import source_provenance, utc_now
from daengs_evals.answer_quality.questions import file_sha256
from daengs_evals.calibration.stats import wilson_interval
from daengs_evals.deferral.rubric import ASSETS_DIR

STRATA: tuple[str, ...] = (
    "emergency",
    "emergency_paraphrase",
    "symptom_nonurgent",
    "trap_keyword",
    "daily",
)


class Sentence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    text: str = Field(min_length=1)
    expect: bool
    stratum: str
    author: str
    note: str | None = None


def load_sentences(path: Path) -> list[Sentence]:
    out: list[Sentence] = []
    seen: set[str] = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        s = Sentence.model_validate(json.loads(line))
        if s.stratum not in STRATA:
            raise ValueError(f"{s.id}: 모르는 층 {s.stratum!r}")
        if s.id in seen:
            raise ValueError(f"문장 id 중복: {s.id}")
        seen.add(s.id)
        out.append(s)
    return out


def matched_terms(text: str) -> dict[str, list[str]]:
    """어느 단어가 걸렸나 — 오탐을 고칠 때 보는 자리. `is_emergency` 와 같은 부분일치."""
    return {
        "high": [t for t in HIGH_TERMS if t in text],
        "ambiguous": [t for t in AMBIGUOUS_TERMS if t in text],
        "urgency": [t for t in URGENCY_TERMS if t in text],
    }


def evaluate(sentences: Iterable[Sentence]) -> list[dict[str, Any]]:
    rows = []
    for s in sentences:
        got = is_emergency(s.text)
        rows.append(
            {
                "id": s.id,
                "stratum": s.stratum,
                "text": s.text,
                "expect": s.expect,
                "got": got,
                "outcome": _outcome(s.expect, got),
                "matched": matched_terms(s.text),
                "note": s.note,
            }
        )
    return rows


def _outcome(expect: bool, got: bool) -> str:
    if expect and got:
        return "hit"
    if expect and not got:
        return "miss"  # 응급을 놓침 — 가장 비싼 오류
    if not expect and got:
        return "false_alarm"  # 응급 아닌데 병원 목록 — 과잉거절의 코드판
    return "correct_pass"


def summarize(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    by_stratum: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        by_stratum[r["stratum"]][r["outcome"]] += 1
    total = Counter(r["outcome"] for r in rows)
    tp, fn, fp = total["hit"], total["miss"], total["false_alarm"]
    strata = {}
    for name in STRATA:
        c = by_stratum.get(name, Counter())
        n = sum(c.values())
        if not n:
            continue
        positive = name.startswith("emergency")
        ok = c["hit"] if positive else c["correct_pass"]
        strata[name] = {
            "n": n,
            "correct": ok,
            "rate": wilson_interval(ok, n).as_dict(),
            **{k: c[k] for k in ("hit", "miss", "false_alarm", "correct_pass") if c[k]},
        }
    return {
        "n": len(rows),
        "recall": wilson_interval(tp, tp + fn).as_dict() if tp + fn else None,
        "precision": wilson_interval(tp, tp + fp).as_dict() if tp + fp else None,
        "counts": {"hit": tp, "miss": fn, "false_alarm": fp, "correct_pass": total["correct_pass"]},
        "strata": strata,
        "misses": [r for r in rows if r["outcome"] == "miss"],
        "false_alarms": [r for r in rows if r["outcome"] == "false_alarm"],
    }


def _pct(d: dict[str, Any] | None) -> str:
    if not d:
        return "—"
    return f"{d['point'] * 100:.0f}% [{d['low'] * 100:.0f}, {d['high'] * 100:.0f}]"


def render_markdown(s: dict[str, Any], *, label: str, generated_at: str) -> str:
    lines = [
        f"# 응급 어휘 게이트 — `{label}`",
        "",
        f"`orchestration/emergency.is_emergency` · 모델 호출 0 · {generated_at}",
        "",
        "## 층별 — 발표에 쓰는 줄은 이것",
        "",
        "| 층 | 뜻 | 맞음 / n | 비율 [95% CI] |",
        "| --- | --- | --- | --- |",
    ]
    meaning = {
        "emergency": "응급, 사전 단어 있음 → 잡아야",
        "emergency_paraphrase": "응급, 사전 단어 없음 → 잡아야 (재현율 구멍)",
        "symptom_nonurgent": "증상이지만 응급 아님 → 통과시켜야",
        "trap_keyword": "응급 단어 든 비응급 → 통과시켜야 (정밀도 구멍)",
        "daily": "일상 → 통과시켜야",
    }
    for name, d in s["strata"].items():
        lines.append(
            f"| {name} | {meaning[name]} | {d['correct']} / {d['n']} | {_pct(d['rate'])} |"
        )
    c = s["counts"]
    lines += [
        "",
        "## 합계 (층 비율에 따라 움직이니 참고만)",
        "",
        f"- 재현율 {_pct(s['recall'])} · 정밀도 {_pct(s['precision'])}",
        f"- 잡음 {c['hit']} · 놓침 {c['miss']} · 오탐 {c['false_alarm']} · 바르게 통과 {c['correct_pass']}",
        "",
        f"## 놓친 응급 ({len(s['misses'])}) — 가장 비싼 오류",
        "",
    ]
    for r in s["misses"]:
        lines.append(f"- `{r['id']}` {r['text']}" + (f" — {r['note']}" if r["note"] else ""))
    lines += ["", f"## 오탐 ({len(s['false_alarms'])}) — 병원 목록이 나가는 비응급", ""]
    for r in s["false_alarms"]:
        hit = r["matched"]
        why = ", ".join(hit["high"]) or (
            f"{'/'.join(hit['ambiguous'])} + {'/'.join(hit['urgency'])}"
        )
        lines.append(f"- `{r['id']}` {r['text']} ← **{why}**")
    lines += [
        "",
        "## 읽는 법",
        "",
        "- 놓침은 사전에 없는 표현이다. 사전을 늘려서 고친다 — 단, 늘린 단어는 함정 층으로 다시 시험한다.",
        (
            "- 오탐은 대부분 '단어는 있지만 시제·의도가 다른' 문장이다. 사전만으로는 못 가른다 — "
            "예방 질문(왜 위험해요 · 얼마나 먹으면)은 어휘 뒤에 규칙이 하나 더 필요하다."
        ),
        "- 이 표는 판정기 없이 나온다. 코드가 바뀌면 `uv run pytest` 의 회귀 테스트가 먼저 알린다.",
        "",
    ]
    return "\n".join(lines)


def run(sentences_path: Path, *, label: str) -> dict[str, Any]:
    sentences = load_sentences(sentences_path)
    rows = evaluate(sentences)
    summary = summarize(rows)
    generated_at = utc_now()
    out = {
        "label": label,
        "generated_at": generated_at,
        "sentences": str(sentences_path),
        "sentences_sha256": file_sha256(sentences_path),
        "source": source_provenance(),
        "lexicon_size": {
            "high": len(HIGH_TERMS),
            "ambiguous": len(AMBIGUOUS_TERMS),
            "urgency": len(URGENCY_TERMS),
        },
        **summary,
    }
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    (ASSETS_DIR / f"emergency_gate_{label}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md = render_markdown(summary, label=label, generated_at=generated_at)
    (ASSETS_DIR / f"report_emergency_gate_{label}.md").write_text(md, encoding="utf-8")
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="응급 어휘 게이트 시험 (모델 0회)")
    parser.add_argument("--sentences", default=str(ASSETS_DIR / "emergency_gate_v1.jsonl"))
    parser.add_argument("--label", default=None, help="기본은 문장 파일 이름의 버전 (v1)")
    args = parser.parse_args(argv)
    path = Path(args.sentences)
    label = args.label or path.stem.rsplit("_", 1)[-1]
    out = run(path, label=label)
    for name, d in out["strata"].items():
        print(f"  {name:22s} {d['correct']:3d}/{d['n']:<3d} {_pct(d['rate'])}")
    print(
        f"놓침 {out['counts']['miss']} · 오탐 {out['counts']['false_alarm']} "
        f"→ {ASSETS_DIR / f'report_emergency_gate_{label}.md'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
