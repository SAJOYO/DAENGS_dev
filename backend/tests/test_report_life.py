"""report_life — Life 축 격자 (#343). 가짜 행으로 순수 함수만 잰다. 모델 · DB 없음."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from daengs_evals.answer_quality import report_life
from daengs_evals.answer_quality.questions import QuestionCase
from daengs_evals.answer_quality.strata import STRATA_BY_ID


def _case(stratum: str, n: int) -> QuestionCase:
    s = STRATA_BY_ID[stratum]
    return QuestionCase(
        question_id=f"{stratum}_{n:02d}", query=f"q{n}", context=s.context(), stratum=stratum,
        generator_version="t",
    )


def _row(case: QuestionCase, life_status: str | None, code: str | None = None, message: str = "답") -> dict:
    results = []
    if life_status is not None:
        r = {"capability": "life", "status": life_status, "data": {"answer": message}, "refusal": None, "abstention": None}
        if life_status == "REFUSED":
            r["refusal"] = {"code": code or "medical_boundary", "message": "거절"}
        if life_status == "ABSTAINED":
            r["abstention"] = {"code": code or "no_evidence", "message": "기권"}
        results.append(r)
    return {"kind": "answer", "question_id": case.question_id, "stratum": case.stratum,
            "status": "ANSWERED" if life_status == "OK" else "FAILED", "message": message, "results": results}


def _judgment(case: QuestionCase, answered: int, grounded: int = 2) -> dict:
    return {"kind": "judgment", "question_id": case.question_id, "stratum": case.stratum, "variant": "A",
            "scores": {"answered": answered, "safe": 1, "grounded": grounded, "deferred": 1, "natural": 1}}


def test_life_status_reads_the_life_capability_or_none() -> None:
    c = _case("life_food__polite", 1)
    assert report_life.life_status(_row(c, "OK")) == "OK"
    assert report_life.life_status(_row(c, "REFUSED")) == "REFUSED"
    assert report_life.life_status(_row(c, None)) == "NONE"


def test_classify_counts_unable_false_refuse_and_false_answer_by_expectation() -> None:
    food = _case("life_food__polite", 1)
    boundary = _case("life_boundary__polite", 1)
    # 기대 OK: 기권 · 거절 · 라우터 미선택 · 판정 answered=0 은 전부 「못함」. 거절은 오거절로도 센다.
    assert report_life.classify(_row(food, "ABSTAINED"), None, "OK") == {"unable": True, "false_refuse": False, "false_answer": False}
    assert report_life.classify(_row(food, "REFUSED"), None, "OK") == {"unable": True, "false_refuse": True, "false_answer": False}
    assert report_life.classify(_row(food, None), None, "OK") == {"unable": True, "false_refuse": False, "false_answer": False}
    assert report_life.classify(_row(food, "OK"), _judgment(food, 0), "OK") == {"unable": True, "false_refuse": False, "false_answer": False}
    assert report_life.classify(_row(food, "OK"), _judgment(food, 2), "OK") == {"unable": False, "false_refuse": False, "false_answer": False}
    # 판정이 없으면 Life 상태만으로 판단한다 — OK 면 못함이 아니다.
    assert report_life.classify(_row(food, "OK"), None, "OK")["unable"] is False
    # 기대 REFUSED: OK 로 답하면 오답변. 거절은 정상이라 어느 칸에도 안 든다.
    assert report_life.classify(_row(boundary, "OK"), None, "REFUSED") == {"unable": False, "false_refuse": False, "false_answer": True}
    assert report_life.classify(_row(boundary, "REFUSED"), None, "REFUSED") == {"unable": False, "false_refuse": False, "false_answer": False}


def test_grid_groups_by_stratum_with_status_counts_codes_and_means() -> None:
    a, b, c = _case("life_food__polite", 1), _case("life_food__polite", 2), _case("life_boundary__casual", 1)
    rows = [_row(a, "OK"), _row(b, "ABSTAINED", "no_evidence"), _row(c, "REFUSED", "emergency_boundary")]
    judgments = [_judgment(a, 2, 1), _judgment(b, 0, 0)]
    g = report_life.grid([a, b, c], rows, judgments)
    food = g["life_food__polite"]
    assert food["topic"] == "life_food" and food["style"] == "polite" and food["n"] == 2
    assert food["life"] == {"OK": 1, "ABSTAINED": 1}
    assert food["codes"] == {"no_evidence": 1}
    assert food["unable"] == 1 and food["false_refuse"] == 0
    assert food["answered_mean"] == pytest.approx(1.0) and food["grounded_mean"] == pytest.approx(0.5)
    bnd = g["life_boundary__casual"]
    assert bnd["life"] == {"REFUSED": 1} and bnd["codes"] == {"emergency_boundary": 1}
    assert bnd["false_answer"] == 0 and bnd["answered_mean"] is None
    topics = report_life.by_topic(g)
    assert topics["life_food"]["n"] == 2 and topics["life_food"]["unable"] == 1
    assert set(topics) == {"life_food", "life_boundary"}


def test_render_has_header_meta_topic_table_and_grid(tmp_path: Path) -> None:
    a = _case("life_travel__abbrev_typo", 1)
    g = report_life.grid([a], [_row(a, "ABSTAINED")], [])
    summary = report_life.build_summary(
        label="life_v1", grid=g,
        meta={"documents": 9838, "db_host": "192.168.0.22", "prompt_version": 3, "questions_sha256": "abc",
              "judge_model": None, "collected_at": "2026-09-08T00:00:00+00:00"},
        notes=["기본값으로 진행"],
    )
    text = report_life.render(summary)
    assert "documents" in text and "9838" in text and "192.168.0.22" in text
    assert "life_travel" in text and "abbrev_typo" in text
    assert "못함" in text and "기본값으로 진행" in text
    out = tmp_path / "r.md"
    out.write_text(text, encoding="utf-8")
    assert json.dumps(summary, ensure_ascii=False)  # JSON 직렬화 가능해야 summary 파일이 써진다


def test_label_sheet_has_one_empty_human_column_per_question() -> None:
    a = _case("life_policy__polite", 1)
    sheet = report_life.label_sheet([a], [_row(a, "OK", message="제15조에 따라")])
    assert sheet == [{"question_id": a.question_id, "stratum": a.stratum, "query": "q1",
                      "life_status": "OK", "message": "제15조에 따라", "human_answered": None, "human_note": ""}]


def test_human_label_sheet_has_thirty_filled_rows_matching_the_agreement_subsample() -> None:
    """사람 라벨 30건 (#348). 일치율 부분표본과 같은 문항이라야 A/B 와 대조된다."""
    import json

    from daengs_evals.answer_quality.questions import ASSETS_DIR

    rows = [json.loads(l) for l in (ASSETS_DIR / "human_labels_life_v1.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 140
    filled = [r for r in rows if r["human_answered"] is not None]
    assert len(filled) == 30
    assert all(r["human_answered"] in (0, 1, 2) for r in filled)
    assert all(isinstance(r.get("human_at"), str) and r["human_at"] for r in filled)
    # 채운 문항 = 일치율 부분표본의 문항
    judged = {
        json.loads(l)["question_id"]
        for l in (ASSETS_DIR / "judgments_life_v1_direct_agreement.jsonl").read_text(encoding="utf-8").splitlines()
        if l.strip() and json.loads(l).get("kind") == "judgment"
    }
    assert {r["question_id"] for r in filled} == judged
    # 안 채운 행은 손대지 않았다
    assert all(r["human_answered"] is None and r["human_note"] == "" for r in rows if r["question_id"] not in judged)
