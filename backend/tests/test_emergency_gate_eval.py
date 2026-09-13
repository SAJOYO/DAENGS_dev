"""응급 어휘 게이트 시험 — 모델 없이 도는 부분 전부."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daengs_evals.deferral.emergency_gate import (
    STRATA,
    Sentence,
    evaluate,
    load_sentences,
    matched_terms,
    summarize,
)

ASSET = Path(__file__).resolve().parents[1] / "evals" / "deferral" / "emergency_gate_v1.jsonl"


def test_asset_loads_and_covers_every_stratum() -> None:
    rows = load_sentences(ASSET)
    assert len(rows) >= 100
    assert {r.stratum for r in rows} == set(STRATA)
    # 응급 층은 전부 True, 나머지는 전부 False — 층 이름이 곧 정답이다
    for r in rows:
        assert r.expect == r.stratum.startswith("emergency"), r.id


def test_duplicate_id_rejected(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    row = {"id": "x_01", "text": "발작", "expect": True, "stratum": "emergency", "author": "t"}
    p.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="중복"):
        load_sentences(p)


def test_outcomes_and_summary_shape() -> None:
    rows = evaluate(
        [
            Sentence(id="a", text="발작을 해요", expect=True, stratum="emergency", author="t"),
            Sentence(
                id="b", text="숨이 가빠요", expect=True, stratum="emergency_paraphrase", author="t"
            ),
            Sentence(
                id="c",
                text="초콜릿이 왜 위험해요",
                expect=False,
                stratum="trap_keyword",
                author="t",
            ),
            Sentence(id="d", text="산책 몇 번", expect=False, stratum="daily", author="t"),
        ]
    )
    assert [r["outcome"] for r in rows] == ["hit", "miss", "false_alarm", "correct_pass"]
    s = summarize(rows)
    assert s["counts"] == {"hit": 1, "miss": 1, "false_alarm": 1, "correct_pass": 1}
    assert s["recall"]["point"] == 0.5 and s["precision"]["point"] == 0.5
    assert s["strata"]["trap_keyword"]["correct"] == 0
    assert s["false_alarms"][0]["matched"]["high"] == ["초콜릿"]


def test_matched_terms_explains_the_combo_rule() -> None:
    m = matched_terms("밤새 계속 토해요")
    assert m["high"] == [] and "토해" in m["ambiguous"] and "계속" in m["urgency"]


def test_known_gate_behaviour_is_pinned() -> None:
    """게이트가 바뀌면 여기가 먼저 깨진다 — 사전을 고친 사람이 이 시험 결과도 같이 보게."""
    rows = {r["id"]: r for r in evaluate(load_sentences(ASSET))}
    assert rows["eg_emerg_01"]["got"] is True  # 발작
    assert rows["eg_trap_01"]["got"] is True  # "초콜릿이 왜 위험해요" — 오탐, 2026-09-11 dev 기준
    assert rows["eg_trap_06"]["got"] is False  # "주워 먹지 말라고" — 시제로 걸러짐
    assert rows["eg_symp_01"]["got"] is False  # 어제 한 번 토함
