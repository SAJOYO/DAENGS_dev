"""셀 옮겨 심기 — 질문 문장이 글자까지 같을 때만, 출처를 남기고. 모델 없이 돈다."""

from __future__ import annotations

import json
from pathlib import Path

from daengs_evals.profile_fitness import collect as collect_mod
from daengs_evals.profile_fitness.collect import load_cells, seed_cells
from daengs_evals.profile_fitness.questions import Question


def _write_cells(path: Path, questions_path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as h:
        h.write(
            json.dumps({"kind": "meta", "questions_path": str(questions_path)}, ensure_ascii=False)
            + "\n"
        )
        for r in rows:
            h.write(json.dumps({"kind": "cell", **r}, ensure_ascii=False) + "\n")


def test_seed_copies_only_identical_query_text(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(collect_mod, "ASSETS_DIR", tmp_path)
    # 출처 label 의 질문 파일 · 셀
    src_q = tmp_path / "q_src.jsonl"
    src_q.write_text(
        "\n".join(
            json.dumps(r, ensure_ascii=False)
            for r in [
                {
                    "question_id": "pf_a_01",
                    "query": "산책 얼마나?",
                    "kind": "reactive",
                    "tier": "coarse",
                    "arms": ["x", "y"],
                    "sensitive_to": ["age_months"],
                    "paraphrase_of": None,
                    "author": "t",
                },
                {
                    "question_id": "pf_b_01",
                    "query": "초콜릿 왜 위험?",
                    "kind": "invariant",
                    "tier": "fine",
                    "arms": ["x", "y"],
                    "sensitive_to": [],
                    "paraphrase_of": None,
                    "author": "t",
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _write_cells(
        tmp_path / "cells_src.jsonl",
        src_q,
        [
            {
                "question_id": "pf_a_01",
                "arm": "x",
                "run": 0,
                "status": "ANSWERED",
                "message": "답1",
                "collected_at": "t0",
            },
            {
                "question_id": "pf_a_01",
                "arm": "y",
                "run": 0,
                "status": "ANSWERED",
                "message": "답2",
                "collected_at": "t0",
            },
            {
                "question_id": "pf_a_01",
                "arm": "x",
                "run": 1,
                "status": "FAILED",
                "message": "",
            },  # 실패한 건 안 옮긴다
            {
                "question_id": "pf_b_01",
                "arm": "x",
                "run": 0,
                "status": "REFUSED",
                "message": "거절",
            },
        ],
    )
    # 새 label: pf_a 는 문장이 같고 id 도 같다. pf_b 는 문장이 바뀌었다 (초콜릿 → 사료 그릇)
    planned = [
        (
            Question(
                question_id="pf_a_01",
                query="산책 얼마나?",
                kind="reactive",
                tier="coarse",
                arms=["x", "y"],
                sensitive_to=["age_months"],
                author="t",
            ),
            "x",
            0,
        ),
        (
            Question(
                question_id="pf_a_01",
                query="산책 얼마나?",
                kind="reactive",
                tier="coarse",
                arms=["x", "y"],
                sensitive_to=["age_months"],
                author="t",
            ),
            "y",
            0,
        ),
        (
            Question(
                question_id="pf_a_01",
                query="산책 얼마나?",
                kind="reactive",
                tier="coarse",
                arms=["x", "y"],
                sensitive_to=["age_months"],
                author="t",
            ),
            "x",
            1,
        ),
        (
            Question(
                question_id="pf_c_01",
                query="사료 그릇 재질?",
                kind="invariant",
                tier="fine",
                arms=["x", "y"],
                author="t",
            ),
            "x",
            0,
        ),
    ]
    dst = tmp_path / "cells_dst.jsonl"
    _write_cells(dst, tmp_path / "q_dst.jsonl", [])
    seeded = seed_cells(dst, "src", planned, set(), tmp_path / "q_dst.jsonl", log=lambda _s: None)
    assert seeded == {("pf_a_01", "x", 0), ("pf_a_01", "y", 0)}
    _, rows = load_cells(dst)
    assert all(r["seeded_from"]["label"] == "src" for r in rows)
    assert {r["message"] for r in rows} == {"답1", "답2"}


def test_seed_skips_cells_already_done(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(collect_mod, "ASSETS_DIR", tmp_path)
    src_q = tmp_path / "q.jsonl"
    src_q.write_text(
        json.dumps(
            {
                "question_id": "pf_a_01",
                "query": "산책?",
                "kind": "reactive",
                "tier": "coarse",
                "arms": ["x", "y"],
                "sensitive_to": ["age_months"],
                "paraphrase_of": None,
                "author": "t",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_cells(
        tmp_path / "cells_src.jsonl",
        src_q,
        [{"question_id": "pf_a_01", "arm": "x", "run": 0, "status": "ANSWERED", "message": "답"}],
    )
    q = Question(
        question_id="pf_a_01",
        query="산책?",
        kind="reactive",
        tier="coarse",
        arms=["x", "y"],
        sensitive_to=["age_months"],
        author="t",
    )
    dst = tmp_path / "cells_dst.jsonl"
    _write_cells(dst, src_q, [])
    assert (
        seed_cells(dst, "src", [(q, "x", 0)], {("pf_a_01", "x", 0)}, src_q, log=lambda _s: None)
        == set()
    )
