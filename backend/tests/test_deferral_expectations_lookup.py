"""기대 파일 빌려 쓰기 — 질문을 실제로 덮는 파일을 고르는가 (2026-09-12 에 첫 글롭을 돌려줘 40셀이 라벨 없음이 됐다)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from daengs_evals.deferral import judge


def _write(path: Path, ids: list[str]) -> None:
    rows = [
        {
            "question_id": i,
            "expect": "answer",
            "expected_reason": "none",
            "rationale": "t",
            "review": False,
        }
        for i in ids
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_borrows_the_file_that_covers_the_questions(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(judge, "ASSETS_DIR", tmp_path)
    _write(tmp_path / "expectations_pf_ask_v1.jsonl", ["ask_1", "ask_2"])  # 글롭에서 먼저 온다
    _write(tmp_path / "expectations_pf_v6_sub18.jsonl", ["walk_1", "food_1"])
    monkeypatch.setattr(
        judge,
        "load_questions",
        lambda _p: [SimpleNamespace(question_id="walk_1"), SimpleNamespace(question_id="food_1")],
    )
    got = judge._resolve_expectations("pf_v7_sub18", Path("x.jsonl"))
    assert got.name == "expectations_pf_v6_sub18.jsonl"


def test_own_label_wins_and_no_cover_raises(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(judge, "ASSETS_DIR", tmp_path)
    _write(tmp_path / "expectations_pf_ask_v1.jsonl", ["ask_1"])
    monkeypatch.setattr(judge, "load_questions", lambda _p: [SimpleNamespace(question_id="zzz")])
    with pytest.raises(FileNotFoundError):
        judge._resolve_expectations("pf_other", Path("x.jsonl"))
    _write(tmp_path / "expectations_pf_other.jsonl", ["zzz"])
    assert (
        judge._resolve_expectations("pf_other", Path("x.jsonl")).name
        == "expectations_pf_other.jsonl"
    )
