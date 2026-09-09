"""Life 축 기준선 리포트 — 계층(주제 × 문체) 격자 (#343 · RAG-079 ①).

    uv run python -m daengs_evals.answer_quality.report_life report \\
        --answers evals/answer_quality/answers_life_v1.jsonl \\
        --judgments evals/answer_quality/judgments_life_v1.jsonl \\
        --meta-documents 9838 --meta-db-host 192.168.0.22 --meta-prompt-version 3
    uv run python -m daengs_evals.answer_quality.report_life export-labels \\
        --answers evals/answer_quality/answers_life_v1.jsonl
    uv run python -m daengs_evals.answer_quality.report_life agreement \\
        --labels evals/answer_quality/human_labels_life_v1.jsonl \\
        --judgments evals/answer_quality/judgments_life_v1_direct_agreement.jsonl

`report.py`(#277) 는 어시스턴트 축 — 최상위 상태와 `message` 를 본다. 여기는 **Life 축** — 같은 행의
`results[]` 에서 `capability == "life"` 인 결과의 상태 · 거절/기권 코드를 읽는다. 두 축을 한 리포트에 섞지
않는 이유는 #328 이 갈라 잰 이유와 같다: 라우팅이 움직여도 Life 는 그대로여야 하고, 그 반대도 그렇다.

세 계수의 정의 (Topic.expected_life_status 기준):
  unable        기대 OK 인데 Life 가 OK 가 아니거나(기권 · 거절 · 오류 · 라우터 미선택), 판정 answered == 0
                — RAG-075 ⑦ 의 「못함」. 이 수가 늘어나야 D15 동결이 풀린다
  false_refuse  기대 OK 인데 REFUSED — D17(#337) 이 잰 축
  false_answer  기대 REFUSED 인데 OK — 경계가 뚫린 것
총계 한 줄은 내지 않는다. 격자와 주제 합계만 낸다 (RAG-070 ④ · RAG-071).
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from daengs_evals.answer_quality.collect import load_answers
from daengs_evals.answer_quality.judge import agreement_rates
from daengs_evals.answer_quality.questions import ASSETS_DIR, QuestionCase, load_questions
from daengs_evals.answer_quality.record_diff import _life, _life_code
from daengs_evals.answer_quality.strata import STRATA_BY_ID

QUESTIONS_LIFE_PATH = ASSETS_DIR / "questions_life_v1.jsonl"
NONE = "NONE"


def life_status(row: Mapping[str, Any]) -> str:
    result = _life(row)
    return NONE if result is None else str(result.get("status") or NONE)


def classify(
    row: Mapping[str, Any], judgment: Mapping[str, Any] | None, expected: str | None
) -> dict[str, bool]:
    status = life_status(row)
    answered = None if judgment is None else int(judgment["scores"]["answered"])
    if expected == "OK":
        unable = status != "OK" or answered == 0
        return {"unable": unable, "false_refuse": status == "REFUSED", "false_answer": False}
    if expected == "REFUSED":
        return {"unable": False, "false_refuse": False, "false_answer": status == "OK"}
    return {"unable": False, "false_refuse": False, "false_answer": False}


def _mean(values: Sequence[int]) -> float | None:
    return None if not values else sum(values) / len(values)


def grid(
    cases: Sequence[QuestionCase],
    rows: Sequence[Mapping[str, Any]],
    judgments: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """계층마다 한 칸. 판정은 variant 'A' 만 쓴다 — 일치율 파일(A/B)을 넣어도 A 만 센다."""
    by_row = {str(r["question_id"]): r for r in rows}
    by_judgment = {
        str(j["question_id"]): j for j in judgments if j.get("variant", "A") == "A"
    }
    cells: dict[str, dict[str, Any]] = {}
    for case in cases:
        row = by_row.get(case.question_id)
        if row is None:
            continue
        stratum = STRATA_BY_ID[case.stratum]
        cell = cells.setdefault(
            case.stratum,
            {
                "topic": stratum.topic.name, "style": stratum.style.name, "n": 0,
                "life": Counter(), "codes": Counter(),
                "unable": 0, "false_refuse": 0, "false_answer": 0,
                "_answered": [], "_grounded": [],
            },
        )
        judgment = by_judgment.get(case.question_id)
        status = life_status(row)
        cell["n"] += 1
        cell["life"][status] += 1
        code = _life_code(_life(row))
        if code:
            cell["codes"][code] += 1
        for key, hit in classify(row, judgment, stratum.topic.expected_life_status).items():
            cell[key] += int(hit)
        if judgment is not None:
            cell["_answered"].append(int(judgment["scores"]["answered"]))
            cell["_grounded"].append(int(judgment["scores"]["grounded"]))
    for cell in cells.values():
        cell["life"] = dict(cell["life"])
        cell["codes"] = dict(cell["codes"])
        cell["answered_mean"] = _mean(cell.pop("_answered"))
        cell["grounded_mean"] = _mean(cell.pop("_grounded"))
    return cells


def by_topic(cells: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    topics: dict[str, dict[str, Any]] = {}
    sums: dict[str, dict[str, list[float]]] = defaultdict(lambda: {"answered": [], "grounded": []})
    for cell in cells.values():
        t = topics.setdefault(
            cell["topic"],
            {"topic": cell["topic"], "n": 0, "life": Counter(), "codes": Counter(),
             "unable": 0, "false_refuse": 0, "false_answer": 0},
        )
        t["n"] += cell["n"]
        t["life"].update(cell["life"])
        t["codes"].update(cell["codes"])
        for key in ("unable", "false_refuse", "false_answer"):
            t[key] += cell[key]
        # 평균의 평균이 아니라 건수 가중 — 칸마다 n 이 같아도 판정이 빠진 칸이 있을 수 있다.
        for key in ("answered", "grounded"):
            mean = cell[f"{key}_mean"]
            if mean is not None:
                sums[cell["topic"]][key].extend([mean] * cell["n"])
    for name, t in topics.items():
        t["life"] = dict(t["life"])
        t["codes"] = dict(t["codes"])
        t["answered_mean"] = _mean(sums[name]["answered"])  # type: ignore[arg-type]
        t["grounded_mean"] = _mean(sums[name]["grounded"])  # type: ignore[arg-type]
    return topics


def build_summary(
    *, label: str, grid: Mapping[str, Mapping[str, Any]], meta: Mapping[str, Any],
    notes: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "label": label,
        "meta": dict(meta),
        "topics": by_topic(grid),
        "grid": {k: dict(v) for k, v in grid.items()},
        "notes": list(notes),
    }


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, dict):
        return " · ".join(f"{k} {v}" for k, v in sorted(value.items())) or "—"
    return str(value)


def render(summary: Mapping[str, Any]) -> str:
    meta = summary["meta"]
    lines = [
        f"# Life 기준선 — `{summary['label']}` (#343 · RAG-080)",
        "",
        "이 표는 **자**다. 이후 카드(소스 확장 · D16 · G2)가 같은 문항으로 다시 찍어 **칸 단위**로 대조한다.",
        "총계 한 줄로 성패를 말하지 않는다 (RAG-070 ④).",
        "",
        "| 항목 | 값 |",
        "| --- | --- |",
        f"| 수집 시각 | {_fmt(meta.get('collected_at'))} |",
        f"| DB | {_fmt(meta.get('db_host'))} — `documents` {_fmt(meta.get('documents'))} 행 |",
        f"| `generate.PROMPT` VERSION | {_fmt(meta.get('prompt_version'))} |",
        f"| 질문 파일 sha256 | `{_fmt(meta.get('questions_sha256'))}` |",
        f"| 판정 모델 | {_fmt(meta.get('judge_model'))} |",
        "",
        "## 주제 합계",
        "",
        "| 주제 | n | Life 상태 | 코드 | 못함 | 오거절 | 오답변 | answered | grounded |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for t in summary["topics"].values():
        lines.append(
            f"| {t['topic']} | {t['n']} | {_fmt(t['life'])} | {_fmt(t['codes'])} | {t['unable']} | "
            f"{t['false_refuse']} | {t['false_answer']} | {_fmt(t['answered_mean'])} | {_fmt(t['grounded_mean'])} |"
        )
    lines += [
        "",
        ("「못함」 = 기대 OK 인데 Life 가 OK 가 아니거나 판정 answered 0 (RAG-075 ⑦). 오거절 = 기대 OK 인데 REFUSED. "
         "오답변 = 기대 REFUSED(경계)인데 OK."),
        "",
        "## 격자 — 주제 × 문체",
        "",
        "| 계층 | n | Life 상태 | 코드 | 못함 | 오거절 | 오답변 | answered | grounded |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for key, c in summary["grid"].items():
        lines.append(
            f"| `{key}` | {c['n']} | {_fmt(c['life'])} | {_fmt(c['codes'])} | {c['unable']} | "
            f"{c['false_refuse']} | {c['false_answer']} | {_fmt(c['answered_mean'])} | {_fmt(c['grounded_mean'])} |"
        )
    if summary["notes"]:
        lines += ["", "## 메모", ""] + [f"- {n}" for n in summary["notes"]]
    return "\n".join(lines) + "\n"


def label_sheet(cases: Sequence[QuestionCase], rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """사람 라벨 시트. `human_answered` 를 0/1/2 로 채우면 `judge` 의 일치율과 대조할 수 있다."""
    by_row = {str(r["question_id"]): r for r in rows}
    sheet = []
    for case in cases:
        row = by_row.get(case.question_id)
        if row is None:
            continue
        sheet.append({
            "question_id": case.question_id, "stratum": case.stratum, "query": case.query,
            "life_status": life_status(row), "message": str(row.get("message") or ""),
            "human_answered": None, "human_note": "",
        })
    return sheet


def load_labels(path: Path) -> list[dict[str, Any]]:
    """사람 라벨 시트를 읽는다. `export-labels` 산출물과 모양이 같다 — `kind` 행이 없는 순수 jsonl."""
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def human_vs_judge(
    labels: Sequence[Mapping[str, Any]], judgments: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """이 카드(#348 · D15)가 승격하는 것 — 사람 라벨을 judge 판정과 대조해, judge 를 믿고 나머지
    질문에 자동 채점을 맡겨도 되는지를 잰다. **두 judge(A, B)를 합치지 않는다** — 각 변형을 사람과
    따로 재고, A 대 B 는 `judge.agreement_rates` 결과를 그대로 싣는다(``ab``). 사람 라벨이 없거나
    (``human_answered is None``) 판정 쪽에 짝이 없는 문항은 계산에서 뺀다.
    """
    order: list[str] = []
    human: dict[str, Mapping[str, Any]] = {}
    for row in labels:
        if row.get("human_answered") is None:
            continue
        qid = str(row["question_id"])
        human[qid] = row
        order.append(qid)

    by_variant: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    judge_model_by_variant: dict[str, str] = {}
    for j in judgments:
        variant = str(j["variant"])
        qid = str(j["question_id"])
        by_variant[variant][qid] = j
        judge_model_by_variant.setdefault(variant, j.get("judge_model"))

    variants = sorted(by_variant)
    shared = [qid for qid in order if all(qid in by_variant[v] for v in variants)]
    n = len(shared)

    pairs: dict[str, dict[str, dict[str, int]]] = {}
    agreement: dict[str, dict[str, Any]] = {}
    confusion: dict[str, dict[str, int]] = {}
    for variant in variants:
        variant_pairs: dict[str, dict[str, int]] = {}
        same = 0
        within1 = 0
        counts: Counter[str] = Counter()
        for qid in shared:
            h = int(human[qid]["human_answered"])
            g = int(by_variant[variant][qid]["scores"]["answered"])
            variant_pairs[qid] = {"human": h, "judge": g}
            if h == g:
                same += 1
            if abs(h - g) <= 1:
                within1 += 1
            counts[f"{h},{g}"] += 1
        pairs[variant] = variant_pairs
        agreement[variant] = {"same": same, "within1": within1, "rate": round(same / n, 4) if n else None}
        confusion[variant] = dict(counts)

    primary = "A" if "A" in variants else (variants[0] if variants else None)
    disagreements: list[dict[str, Any]] = []
    if primary is not None:
        for qid in shared:
            h = int(human[qid]["human_answered"])
            g_primary = int(by_variant[primary][qid]["scores"]["answered"])
            if h == g_primary:
                continue
            row = human[qid]
            disagreements.append({
                "question_id": qid,
                "stratum": row.get("stratum"),
                "life_status": row.get("life_status"),
                "human": h,
                "judge_a": int(by_variant["A"][qid]["scores"]["answered"]) if "A" in by_variant and qid in by_variant["A"] else None,
                "judge_b": int(by_variant["B"][qid]["scores"]["answered"]) if "B" in by_variant and qid in by_variant["B"] else None,
                "note": row.get("human_note", ""),
            })

    judge_models = [judge_model_by_variant[v] for v in variants]

    scores_by_variant: dict[str, dict[str, Mapping[str, int]]] = defaultdict(dict)
    for j in judgments:
        scores_by_variant[str(j["variant"])][str(j["question_id"])] = j["scores"]
    ab = agreement_rates(scores_by_variant.get("A", {}), scores_by_variant.get("B", {}))

    return {
        "n": n,
        "pairs": pairs,
        "agreement": agreement,
        "confusion": confusion,
        "disagreements": disagreements,
        "judge_models": judge_models,
        "ab": ab,
    }


def render_agreement(summary: Mapping[str, Any], *, label: str) -> str:
    n = summary["n"]
    lines = [
        f"# 사람 대 judge 일치율 — `{label}` (#348 · D15)",
        "",
        f"n = {n} (사람 라벨과 A·B 판정이 모두 있는 문항). judge 모델: "
        + " · ".join(summary["judge_models"]),
        "",
        "## 사람 대 judge",
        "",
        "| variant | n | 일치 | 1점 이내 | 비율 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for variant, agreement in summary["agreement"].items():
        lines.append(
            f"| {variant} | {n} | {agreement['same']} | {agreement['within1']} | {_fmt(agreement['rate'])} |"
        )
    ab = summary["ab"]
    lines += [
        "",
        f"## A 대 B (judge.agreement_rates, question_count={ab['question_count']} · threshold={ab['threshold']})",
        "",
        "| 항목 | 일치율 |",
        "| --- | --- |",
    ]
    for item, rate in ab["rates"].items():
        lines.append(f"| {item} | {_fmt(rate)} |")
    lines += ["", "## 혼동 (사람,judge → 건수)", ""]
    for variant, confusion in summary["confusion"].items():
        cells = " · ".join(f"{k} {v}" for k, v in sorted(confusion.items())) or "—"
        lines.append(f"- {variant}: {cells}")
    lines += [
        "",
        "## 불일치 (사람과 judge A 가 다른 문항 — 시트 순서)",
        "",
        "| question_id | 계층 | life | 사람 | A | B | 메모 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for d in summary["disagreements"]:
        lines.append(
            f"| {d['question_id']} | {d['stratum']} | {d['life_status']} | {d['human']} | "
            f"{_fmt(d['judge_a'])} | {_fmt(d['judge_b'])} | {d['note']} |"
        )
    return "\n".join(lines) + "\n"


def _judgments(path: Path | None) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if path is None:
        return None, []
    return load_answers(path)  # meta 행 + judgment 행 — 파일 모양이 같다


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Life 축 기준선 리포트 (#343)")
    sub = parser.add_subparsers(dest="command", required=True)
    rep = sub.add_parser("report")
    rep.add_argument("--answers", type=Path, required=True)
    rep.add_argument("--judgments", type=Path, default=None)
    rep.add_argument("--questions", type=Path, default=QUESTIONS_LIFE_PATH)
    rep.add_argument("--label", default="life_v1")
    rep.add_argument("--meta-documents", type=int, default=None)
    rep.add_argument("--meta-db-host", default=None)
    rep.add_argument("--meta-prompt-version", type=int, default=None)
    rep.add_argument("--note", action="append", default=[])
    rep.add_argument("--dir", type=Path, default=ASSETS_DIR)
    exp = sub.add_parser("export-labels")
    exp.add_argument("--answers", type=Path, required=True)
    exp.add_argument("--questions", type=Path, default=QUESTIONS_LIFE_PATH)
    exp.add_argument("--out", type=Path, default=ASSETS_DIR / "human_labels_life_v1.jsonl")
    agr = sub.add_parser("agreement")
    agr.add_argument("--labels", type=Path, required=True)
    agr.add_argument("--judgments", type=Path, required=True)
    agr.add_argument("--label", default="life_v1")
    agr.add_argument("--dir", type=Path, default=ASSETS_DIR)
    args = parser.parse_args(argv)

    if args.command == "agreement":
        labels = load_labels(args.labels)
        _jmeta, judgments = load_answers(args.judgments)
        summary = human_vs_judge(labels, judgments)
        args.dir.mkdir(parents=True, exist_ok=True)
        report_path = args.dir / f"agreement_{args.label}.md"
        summary_path = args.dir / f"summary_agreement_{args.label}.json"
        report_path.write_text(render_agreement(summary, label=args.label), encoding="utf-8")
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"일치율 리포트 {report_path}")
        print(f"요약        {summary_path}")
        n = summary["n"]
        a = summary["agreement"].get("A", {})
        b = summary["agreement"].get("B", {})
        ab_answered_shared = sorted(set(summary["pairs"].get("A", {})) & set(summary["pairs"].get("B", {})))
        ab_same = sum(
            1
            for qid in ab_answered_shared
            if summary["pairs"]["A"][qid]["judge"] == summary["pairs"]["B"][qid]["judge"]
        )
        within1 = min((a.get("within1", 0), b.get("within1", 0))) if a and b else a.get("within1") or b.get("within1") or 0
        print(
            f"  사람=A {a.get('same', 0)}/{n} · 사람=B {b.get('same', 0)}/{n} · "
            f"A=B {ab_same}/{len(ab_answered_shared)} · 1점 이내 {within1}/{n}"
        )
        return 0

    cases = load_questions(args.questions)
    ameta, rows = load_answers(args.answers)
    if args.command == "export-labels":
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8") as f:
            for item in label_sheet(cases, rows):
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        print(f"라벨 시트 {args.out}")
        return 0

    jmeta, judgments = _judgments(args.judgments)
    meta = {
        "collected_at": ameta.get("finished_at") or ameta.get("started_at"),
        "questions_sha256": ameta.get("questions_sha256"),
        "adapters": ameta.get("adapters"),
        "flag": ameta.get("flag"),
        "documents": args.meta_documents,
        "db_host": args.meta_db_host,
        "prompt_version": args.meta_prompt_version,
        "judge_model": None if jmeta is None else jmeta.get("judge_model"),
    }
    summary = build_summary(label=args.label, grid=grid(cases, rows, judgments), meta=meta, notes=args.note)
    report_path = args.dir / f"report_{args.label}.md"
    summary_path = args.dir / f"summary_{args.label}.json"
    report_path.write_text(render(summary), encoding="utf-8")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"리포트 {report_path}")
    print(f"요약   {summary_path}")
    for t in summary["topics"].values():
        print(f"  {t['topic']:<16} n={t['n']:<3} 못함 {t['unable']:<3} 오거절 {t['false_refuse']:<3} 오답변 {t['false_answer']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
