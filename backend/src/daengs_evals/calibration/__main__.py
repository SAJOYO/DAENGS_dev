"""사람 라벨 — 선별 · 시트 · κ · 게이트.

    uv run python -m daengs_evals.calibration review --axis profile_fitness --label pf_v1
    uv run python -m daengs_evals.calibration kappa  --axis profile_fitness --label pf_v1 --labeler hong
    uv run python -m daengs_evals.calibration gate   --axis profile_fitness --label pf_v1

`review` 는 판정 파일에서 두 블록(자기모순 · 무작위) + 반복 10건을 골라 **블라인드 시트**를 낸다.
사람이 시트를 채우면 `kappa` 가 뒤집기를 되돌려 라벨 파일을 쓰고, 블록별 κ 와 본인 일관성을 낸다.
`gate` 는 그 라벨로 항목별 통과/보류를 출력하고 미달이면 exit 1 — CI 에서 그대로 쓴다.

`training_quality/__main__.py` 와 같은 모양(패키지 `__main__` + 서브커맨드).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from daengs_evals.calibration.agreement import Agreement
from daengs_evals.calibration.gate import Thresholds, require_calibration
from daengs_evals.calibration.labels import intra_rater, read_labels, write_labels
from daengs_evals.calibration.select import export_sheet, select_for_labeling, summarize_selection

AXES = ("profile_fitness",)


def _paths(axis: str, label: str) -> dict[str, Path]:
    from daengs_evals.profile_fitness.profiles import ASSETS_DIR

    base = ASSETS_DIR.parent / "calibration"
    return {
        "sheet": base / "sheets" / f"{axis}__{label}__sheet.jsonl",
        "key": base / "sheets" / f"{axis}__{label}__key.jsonl",
        "labels": base / "human_labels" / f"{axis}__{label}.jsonl",
        "summary": base / f"agreement_{axis}__{label}.json",
    }


def _load_axis(
    axis: str, label: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None, dict, dict, dict]:
    if axis != "profile_fitness":
        raise SystemExit(f"아직 없는 축: {axis}")
    from daengs_evals.profile_fitness.collect import cells_path, load_cells
    from daengs_evals.profile_fitness.judge import judgments_path
    from daengs_evals.profile_fitness.pairs import index_cells
    from daengs_evals.profile_fitness.profiles import load_profiles, profiles_by_id
    from daengs_evals.profile_fitness.questions import load_questions
    from daengs_evals.profile_fitness.report import load_judgments

    meta, rows, _ = load_judgments(judgments_path(label, "A"))
    b_path = judgments_path(label, "B")
    b_rows = load_judgments(b_path)[1] if b_path.exists() else None
    cmeta, cells = load_cells(cells_path(meta["cells_label"]))
    questions = {q.question_id: q for q in load_questions(Path(cmeta["questions_path"]))}
    for c in cells:
        c["_question"] = questions[c["question_id"]].query
    profiles = profiles_by_id(load_profiles(Path(cmeta["profiles_path"])))
    return rows, b_rows, index_cells(cells), profiles, meta


def cmd_review(args: argparse.Namespace) -> int:
    rows, b_rows, cells, profiles, _ = _load_axis(args.axis, args.label)
    p = _paths(args.axis, args.label)
    already: set[str] = set()
    if args.round > 1 and p["labels"].exists():
        from daengs_evals.calibration.labels import _read as _read_rows

        already = {r["pair_id"] for r in _read_rows(p["labels"])}
        suffix = f"_round{args.round}"
        p["sheet"] = p["sheet"].with_name(p["sheet"].name.replace("__sheet", f"__sheet{suffix}"))
        p["key"] = p["key"].with_name(p["key"].name.replace("__key", f"__key{suffix}"))
    selection = select_for_labeling(
        rows,
        b_rows=b_rows,
        n_contradiction=args.contradiction,
        n_random=args.random,
        n_repeats=args.repeats,
        seed=args.seed + args.round,
        exclude=already,
        only_kind=args.only_kind,
    )
    n, r = export_sheet(
        selection,
        {x["pair_id"]: x for x in rows},
        cells,
        profiles,
        sheet_path=p["sheet"],
        key_path=p["key"],
        seed=args.seed,
    )
    print(json.dumps(summarize_selection(selection), ensure_ascii=False))
    print(f"시트 {n}건 + 반복 {r}건 → {p['sheet']}\n키 → {p['key']}  (라벨러는 키를 열지 않는다)")
    return 0


def _agreements(
    axis: str, label: str, labels: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Agreement]]:
    """블록별 κ. 'all' 은 합산이지만 모집단 추정이 아니다."""
    from daengs_evals.profile_fitness.judge import judgments_path
    from daengs_evals.profile_fitness.report import human_agreement, load_judgments

    rows = load_judgments(judgments_path(label, "A"))[1]
    out: dict[str, dict[str, Agreement]] = {}
    for block in ("random", "contradiction", "all"):
        subset = {k: v for k, v in labels.items() if block == "all" or v.get("block") == block}
        out[block] = human_agreement(rows, subset)["items"] if subset else {}
    return out


def _read_all_rounds(p: dict[str, Path]) -> tuple[dict[str, dict[str, Any]], list]:
    """1회차 시트 + `__sheet_round2` … 를 전부 읽어 합친다. 같은 pair 가 두 번이면 앞 회차가 남는다."""
    labels, repeats = read_labels(p["sheet"], p["key"])
    for sheet in sorted(
        p["sheet"].parent.glob(p["sheet"].name.replace("__sheet.jsonl", "__sheet_round*.jsonl"))
    ):
        key = sheet.with_name(sheet.name.replace("__sheet_round", "__key_round"))
        more, more_rep = read_labels(sheet, key)
        for k, v in more.items():
            labels.setdefault(k, v)
        repeats += more_rep
    return labels, repeats


def cmd_kappa(args: argparse.Namespace) -> int:
    p = _paths(args.axis, args.label)
    labels, repeats = _read_all_rounds(p)
    if not labels:
        print("채워진 라벨이 없다 — 시트를 먼저 채우세요")
        return 1
    write_labels(labels, p["labels"], labeler=args.labeler)
    by_block = _agreements(args.axis, args.label, labels)
    intra = intra_rater(repeats)
    summary = {
        "axis": args.axis,
        "label": args.label,
        "labeler": args.labeler,
        "n_labels": len(labels),
        "n_repeats": len(repeats),
        "kappa_by_block": {
            b: {
                k: {"kappa": v.value, "n": v.n, "undefined": v.undefined, "marginals": v.marginals}
                for k, v in items.items()
            }
            for b, items in by_block.items()
        },
        "intra_rater": {
            k: {"kappa": v.value, "n": v.n, "undefined": v.undefined} for k, v in intra.items()
        },
        "note": "'all' 은 합산이며 모집단 추정이 아니다. 발표는 random 블록 κ 를 쓰고 contradiction 은 약한 자리의 지도다.",
    }
    p["summary"].write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    for block, items in by_block.items():
        line = " · ".join(
            f"{k} κ={v.value:.2f}" if v.value is not None else f"{k} 못 잼({v.undefined})"
            for k, v in items.items()
        )
        print(f"{block:14s} {line}")
    line = " · ".join(
        f"{k} κ={v.value:.2f}" if v.value is not None else f"{k} 못 잼" for k, v in intra.items()
    )
    print(f"{'intra-rater':14s} {line}")
    print(f"→ {p['labels']}\n→ {p['summary']}")
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    p = _paths(args.axis, args.label)
    if not p["summary"].exists():
        print("일치도 요약이 없다 — `kappa` 를 먼저")
        return 1
    labels, _ = _read_all_rounds(p)
    by_block = _agreements(args.axis, args.label, labels)
    items = by_block.get("random") or by_block.get("all") or {}
    # 판정 항목 ↔ 관찰 칸 대응: no_fabrication←fabricated, invariance←changed, responsiveness←responsiveness
    mapping = {
        "no_fabrication": items.get("fabricated"),
        "invariance": items.get("changed"),
        "responsiveness": items.get("responsiveness"),
    }
    gate = require_calibration(
        mapping, Thresholds(kappa_min=args.kappa_min, min_labels=args.min_labels)
    )
    for item, d in gate.decisions.items():
        print(f"  {item:16s} {'통과' if d.passed else '보류 — ' + str(d.reason)}  {d.detail}")
    return 0 if not gate.withheld else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="사람 라벨 캘리브레이션")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--axis", choices=AXES, default="profile_fitness")
        p.add_argument("--label", required=True)

    p_review = sub.add_parser("review")
    common(p_review)
    p_review.add_argument("--contradiction", type=int, default=15)
    p_review.add_argument("--random", type=int, default=25)
    p_review.add_argument("--repeats", type=int, default=10)
    p_review.add_argument("--seed", type=int, default=20260909)
    p_review.add_argument(
        "--round", type=int, default=1, help="2 이상이면 앞 회차 라벨을 뺀 새 시트 (sheet_2 …)"
    )
    p_review.add_argument(
        "--only-kind", default=None, help="reactive 만 등 — responsiveness 라벨을 채울 때"
    )

    p_label = sub.add_parser("label", help="터미널에서 한 쌍씩 0/1 을 받아 시트를 채운다")
    common(p_label)
    p_label.add_argument("--round", type=int, default=1)

    p_kappa = sub.add_parser("kappa")
    common(p_kappa)
    p_kappa.add_argument("--labeler", required=True)

    p_gate = sub.add_parser("gate")
    common(p_gate)
    p_gate.add_argument("--kappa-min", type=float, default=0.6)
    p_gate.add_argument("--min-labels", type=int, default=30)

    args = parser.parse_args(argv)
    if args.command == "label":
        from daengs_evals.calibration.label_cli import run_label

        sheet = _paths(args.axis, args.label)["sheet"]
        if args.round > 1:
            sheet = sheet.with_name(sheet.name.replace("__sheet", f"__sheet_round{args.round}"))
        return run_label(sheet)
    return {"review": cmd_review, "kappa": cmd_kappa, "gate": cmd_gate}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
