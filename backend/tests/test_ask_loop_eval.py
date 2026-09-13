"""되묻기 종료 시험 — 모델 없이 도는 부분."""

from __future__ import annotations

from pathlib import Path

import pytest

from daengs_evals.ask_loop.collect import STRATA, Script, history_of, load_scripts
from daengs_evals.ask_loop.report import outcome_of, summarize

ASSET = Path(__file__).resolve().parents[1] / "evals" / "ask_loop" / "scripts_v1.jsonl"


def test_scripts_load_and_cover_strata() -> None:
    scripts = load_scripts(ASSET)
    assert len(scripts) >= 12
    assert {s.stratum for s in scripts} == set(STRATA)
    for s in scripts:
        # 첫 턴에 둘이 모인 층은 되묻기 없이 닫혀야 한다
        if s.stratum == "two_signs_at_once":
            assert s.must_close_by == 1 and not s.first_should_ask, s.script_id
        # 상태 질문으로 여는 층은 첫 턴 되묻기가 정답이다
        if s.stratum in ("answered_axes", "brief_answer", "topic_switch"):
            assert s.first_should_ask, s.script_id


def test_script_rejects_close_turn_beyond_length() -> None:
    with pytest.raises(ValueError):
        Script(
            script_id="al_x_01",
            stratum="accumulate",
            turns=["토해요"],
            must_close_by=2,
            first_should_ask=False,
            author="t",
        )


def _turn(
    status: str, *, user: str = "u", message: str = "m", question: str | None = None, axes=None
):
    t = {"user": user, "status": status, "message": message, "clarify": None}
    if status == "CLARIFY":
        t["clarify"] = {
            "question": question or "q?",
            "missing": ["observation"],
            "missing_axes": axes or [],
        }
    return t


def test_history_matches_server_rules() -> None:
    # 완료 턴은 전부 앞 대화가 되고, 대기는 마지막이 CLARIFY 일 때만
    done = [
        _turn(
            "CLARIFY",
            user="어때?",
            message="기록이 없어요",
            question="식욕은요?",
            axes=["APPETITE"],
        )
    ]
    turns, pending = history_of("al_axes_01", done)
    assert len(turns) == 1 and turns[0].user == "어때?" and turns[0].assistant == "기록이 없어요"
    assert pending is not None and pending.question == "식욕은요?"
    assert [str(a) for a in pending.missing_axes] == ["APPETITE"]
    assert pending.turn_id == turns[0].turn_id

    done2 = [*done, _turn("ANSWERED", user="잘 먹어요", message="괜찮아 보여요")]
    turns2, pending2 = history_of("al_axes_01", done2)
    assert len(turns2) == 2 and pending2 is None

    # 모르는 항목 이름은 버리고 죽지 않는다
    done3 = [_turn("CLARIFY", question="q", axes=["APPETITE", "NOPE"])]
    _, pending3 = history_of("al_axes_01", done3)
    assert [str(a) for a in pending3.missing_axes] == ["APPETITE"]


def _row(statuses, *, must=2, first=True, questions=None, axes=None):
    turns = []
    for i, s in enumerate(statuses):
        q = (questions or [None] * len(statuses))[i]
        a = (axes or [None] * len(statuses))[i]
        turns.append(_turn(s, user=f"u{i}", question=q, axes=a))
    return {
        "script_id": "al_t_01",
        "stratum": "answered_axes",
        "must_close_by": must,
        "first_should_ask": first,
        "turns": turns,
    }


def test_outcomes() -> None:
    assert outcome_of(_row(["CLARIFY", "ANSWERED"]))["result"] == "closed_on_time"
    assert outcome_of(_row(["CLARIFY", "CLARIFY", "ANSWERED"], must=2))["result"] == "late"
    assert outcome_of(_row(["CLARIFY", "CLARIFY", "CLARIFY"], must=2))["result"] == "never_closed"
    assert outcome_of(_row(["CLARIFY", "FAILED"]))["result"] == "failed"
    # 첫 턴에 둘 — 되묻기 없이 답해야
    assert outcome_of(_row(["CLARIFY"], must=1, first=False))["result"] == "never_closed"
    assert outcome_of(_row(["ANSWERED"], must=1, first=False))["result"] == "closed_on_time"


def test_repeat_and_reask_flags() -> None:
    same = _row(
        ["CLARIFY", "CLARIFY", "ANSWERED"],
        must=3,
        questions=["식욕은 어떤가요?", "식욕은 어떤가요 ?", None],
        axes=[["APPETITE"], ["ENERGY"], None],
    )
    o = outcome_of(same)
    assert o["repeated_question"] is True  # 공백·문장부호만 다르면 같은 질문
    assert o["reasked_axis"] is False
    again = _row(
        ["CLARIFY", "CLARIFY", "ANSWERED"],
        must=3,
        questions=["식욕은요?", "밥은 잘 먹나요?", None],
        axes=[["APPETITE"], ["APPETITE", "STOOL"], None],
    )
    assert outcome_of(again)["reasked_axis"] is True
    # 첫 턴에 되물어야 하는데 바로 답함
    assert outcome_of(_row(["ANSWERED", "ANSWERED"]))["first_ask_ok"] is False
    assert outcome_of(_row(["ANSWERED"], must=1, first=False))["first_ask_ok"] is None


def test_summarize_counts_and_problems() -> None:
    rows = [
        _row(["CLARIFY", "ANSWERED"]),
        _row(["CLARIFY", "CLARIFY"]),
        _row(["CLARIFY", "FAILED"]),
    ]
    s = summarize({"label": "t", "general": {"prompt_version": "v7"}}, rows)
    assert s["n"] == 3 and s["measured"] == 2
    assert s["results"] == {"closed_on_time": 1, "never_closed": 1, "failed": 1}
    assert s["closed_on_time"]["point"] == 0.5
    assert [p["script_id"] for p in s["problems"]] == ["al_t_01"]
    assert s["first_ask"] == {"ok": 2, "n": 2}
