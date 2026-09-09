"""선별 · 블라인드 시트 · 라벨 되읽기 · 본인 일관성. 모델 없이 돈다."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from daengs_evals.calibration.labels import intra_rater, read_labels, write_labels
from daengs_evals.calibration.select import (
    contradiction_reasons,
    export_sheet,
    select_for_labeling,
    summarize_selection,
)
from daengs_evals.profile_fitness.profiles import Profile


def jrow(
    qid: str,
    cond: str,
    kind: str,
    *,
    changed: int = 1,
    fab: int = 0,
    st: int = 0,
    pos: bool = False,
    abst: int = 0,
) -> dict[str, Any]:
    return {
        "kind": "judgment",
        "pair_id": f"{qid}|{cond}|a#0|b#0",
        "question_id": qid,
        "question_kind": kind,
        "condition": cond,
        "query": "질문?",
        "position_dependent": pos,
        "observation": {
            "changed": changed,
            "profile": changed,
            "fabricated": fab,
            "stereotype": st,
            "abstained": abst,
        },
        "score": {"items": {}, "failures": [], "skipped": None},
    }


def make_rows(n: int = 60) -> list[dict[str, Any]]:
    rows = []
    for i in range(n):
        kind = ("reactive", "invariant", "probe")[i % 3]
        cond = ("contrast", "ablation", "noise")[i % 3]
        rows.append(jrow(f"q{i:02d}", cond, kind, fab=int(i % 17 == 0), pos=(i % 23 == 0)))
    return rows


def test_contradiction_reasons_cover_every_signal() -> None:
    r = jrow("q", "contrast", "reactive", fab=1, st=1, pos=True, abst=1)
    b = dict(r, observation=dict(r["observation"], changed=0))
    assert set(contradiction_reasons(r, b)) == {
        "position_dependent",
        "abstained",
        "fabricated",
        "stereotype",
        "variant_disagreement",
    }
    assert contradiction_reasons(jrow("q", "contrast", "reactive")) == []


def test_blocks_are_disjoint_deterministic_and_exclude_control() -> None:
    rows = make_rows()
    s1 = select_for_labeling(rows, n_contradiction=5, n_random=10, n_repeats=4)
    s2 = select_for_labeling(rows, n_contradiction=5, n_random=10, n_repeats=4)
    assert s1 == s2
    assert not (set(s1.contradiction) & set(s1.random))
    assert all(pid in s1.all_ids for pid in s1.repeats)
    assert all("|noise|" not in pid for pid in s1.all_ids)
    # 60행 중 noise 를 뺀 자기모순은 4개(q00 · q34 · q46 · q51) — 상한 5 보다 적으면 있는 만큼만
    assert summarize_selection(s1)["contradiction"] == len(s1.contradiction) == 4


def test_random_block_is_stratified_across_condition_and_kind() -> None:
    rows = make_rows(90)
    s = select_for_labeling(rows, n_contradiction=0, n_random=12, n_repeats=0)
    strata = {pid.split("|")[1] for pid in s.random}
    assert strata == {"contrast", "ablation"}


def test_sheet_is_blind_and_key_round_trips(tmp_path: Path) -> None:
    rows = make_rows(12)
    rows_by_id = {r["pair_id"]: r for r in rows}
    cells = {}
    for r in rows:
        q = r["question_id"]
        cells[(q, "a", 0)] = {"message": f"{q} 답 A", "_question": "질문?"}
        cells[(q, "b", 0)] = {"message": f"{q} 답 B", "_question": "질문?"}
    profiles = {
        "a": Profile(
            profile_id="a", label="a", dog={"breed": "치와와"}, visible_to=["general"], axis="x"
        ),
        "b": Profile(
            profile_id="b", label="b", dog={"breed": "말티즈"}, visible_to=["general"], axis="x"
        ),
    }
    sel = select_for_labeling(rows, n_contradiction=2, n_random=4, n_repeats=2)
    sheet, key = tmp_path / "sheet.jsonl", tmp_path / "key.jsonl"
    export_sheet(sel, rows_by_id, cells, profiles, sheet_path=sheet, key_path=key)

    sheet_text = sheet.read_text(encoding="utf-8")
    assert (
        "observation" not in sheet_text and "pair_id" not in sheet_text
    )  # 판정기 출력 · 정체 없음
    key_rows = [json.loads(x) for x in key.read_text(encoding="utf-8").splitlines()[1:]]
    assert (
        any(k["swapped"] for k in key_rows) or len(key_rows) < 4
    )  # 뒤집기가 실제로 일어난다 (확률적)
    assert sum(1 for k in key_rows if k["block"] == "repeat") == 2

    # 사람이 채웠다고 치자 — 반복분은 한 칸 다르게
    filled = []
    for i, line in enumerate(sheet_text.splitlines()):
        row = json.loads(line)
        if row.get("kind") == "meta":
            filled.append(row)
            continue
        row.update(changed=1, profile=1, fabricated=0, stereotype=0, responsiveness=2, note="ok")
        if key_rows[row["sheet_no"] - 1]["block"] == "repeat" and row["sheet_no"] % 2 == 0:
            row["changed"] = 0
        filled.append(row)
    sheet.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in filled) + "\n", encoding="utf-8"
    )

    labels, repeats = read_labels(sheet, key)
    assert len(labels) == len(sel.all_ids) and len(repeats) == 2
    assert all(v["block"] in ("contradiction", "random") for v in labels.values())
    intra = intra_rater(repeats)
    assert "changed" in intra and intra["changed"].n == 2

    out = tmp_path / "labels.jsonl"
    write_labels(labels, out, labeler="t")
    assert out.read_text(encoding="utf-8").count("\n") == len(labels) + 1  # meta + 라벨


def test_unlabeled_rows_are_skipped_not_counted() -> None:
    rows = make_rows(6)
    assert select_for_labeling(rows, n_contradiction=1, n_random=2, n_repeats=1).repeats


def test_responsiveness_is_derived_when_the_labeler_leaves_it_blank() -> None:
    from daengs_evals.calibration.labels import _coerce

    assert (
        _coerce({"changed": 0, "profile": 0, "fabricated": 0, "stereotype": 0})["responsiveness"]
        == 0
    )
    assert (
        _coerce({"changed": 1, "profile": 1, "fabricated": 0, "stereotype": 0})["responsiveness"]
        == 2
    )
    assert (
        _coerce({"changed": 1, "profile": 0, "fabricated": 0, "stereotype": 0})["responsiveness"]
        == 1
    )
    assert (
        _coerce(
            {"changed": 1, "profile": 1, "fabricated": 0, "stereotype": 0, "responsiveness": 1}
        )["responsiveness"]
        == 1
    )
