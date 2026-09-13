"""시험 ④ 앞 대화 기억 — 모델 없이 도는 부분."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from daengs_evals.turn_relation.collect import (
    STRATA,
    RecordingResolver,
    Script,
    load_scripts,
    prior_turns_of,
)
from daengs_evals.turn_relation.report import got_of, is_correct, summarize

ASSET = Path(__file__).resolve().parents[1] / "evals" / "turn_relation" / "scripts_v1.jsonl"


def test_scripts_load_and_cover_strata() -> None:
    scripts = load_scripts(ASSET)
    assert len(scripts) >= 30
    assert {s.stratum for s in scripts} == set(STRATA)
    # 되묻기 층만 pending 을 갖는다
    for s in scripts:
        assert (s.pending is not None) == (s.stratum == "pending_answer"), s.script_id


def test_duplicate_script_rejected(tmp_path: Path) -> None:
    row = {
        "script_id": "tr_new_01",
        "stratum": "new_unrelated",
        "prior": [{"user": "a", "assistant": "b"}],
        "pending": None,
        "current": "c",
        "expect": "NEW",
        "author": "t",
    }
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="중복"):
        load_scripts(p)


def test_prior_turns_are_deterministic_and_pending_binds_to_last_turn() -> None:
    s = load_scripts(ASSET)
    pend = next(x for x in s if x.pending is not None)
    turns1, pending1 = prior_turns_of(pend)
    turns2, _ = prior_turns_of(pend)
    assert [t.turn_id for t in turns1] == [t.turn_id for t in turns2]
    assert pending1 is not None and pending1.turn_id == turns1[-1].turn_id
    assert pending1.missing == ["observation"]


def test_recording_resolver_keeps_raw_value_and_errors() -> None:
    class Fake:
        async def resolve(self, *, query, candidates, pending):
            if query == "boom":
                raise RuntimeError("provider down")
            return {"relation": "FOLLOW_UP"}

    sink: dict = {}
    rr = RecordingResolver(Fake(), sink)
    assert asyncio.run(rr.resolve(query="q", candidates=[], pending=None)) == {
        "relation": "FOLLOW_UP"
    }
    assert sink["resolve_called"] and sink["resolved"] == {"relation": "FOLLOW_UP"}
    sink.clear()
    with pytest.raises(RuntimeError):
        asyncio.run(rr.resolve(query="boom", candidates=[], pending=None))
    assert sink["error"]["kind"] == "RuntimeError"


def _row(
    expect: str,
    *,
    relation=None,
    conf=None,
    called=True,
    error=None,
    runner_error=None,
    stratum="followup_marker",
):
    return {
        "script_id": "tr_x_01",
        "stratum": stratum,
        "expect": expect,
        "current": "q",
        "observed": {
            "model_called": called,
            "error": error,
            "relation": relation,
            "confidence": conf,
        },
        "runner_error": runner_error,
        "tokens": {
            "resolver": {"in": 1, "out": 1},
            "router": {"in": 1, "out": 1},
            "general": {"in": 0, "out": 0},
        },
        "router_prompt_version": "semantic-router-ko-v10-resolved"
        if relation not in (None, "NEW")
        else "semantic-router-ko-v10",
    }


def test_got_folds_into_what_the_service_uses() -> None:
    f = 0.6
    assert got_of(_row("NEW", relation="NEW", called=False), floor=f) == "FAST_NEW"
    assert got_of(_row("FOLLOW_UP", relation="FOLLOW_UP", conf=0.9), floor=f) == "FOLLOW_UP"
    assert got_of(_row("FOLLOW_UP", relation="FOLLOW_UP", conf=0.4), floor=f) == "LOW_CONFIDENCE"
    assert (
        got_of(_row("META", error={"kind": "TurnResolutionError", "detail": "schema"}), floor=f)
        == "ERROR"
    )
    assert got_of(_row("NEW", runner_error="x"), floor=f) == "RUNNER_ERROR"
    assert is_correct("NEW", "FAST_NEW") and is_correct("NEW", "NEW")
    assert not is_correct("FOLLOW_UP", "FAST_NEW")


def test_summary_counts_and_wiring_check() -> None:
    rows = [
        _row("NEW", relation="NEW", called=False, stratum="new_unrelated"),
        _row("FOLLOW_UP", relation="FOLLOW_UP", conf=0.9),
        _row("FOLLOW_UP", relation="NEW", called=False, stratum="followup_no_marker"),
    ]
    rows[1]["router_prompt_version"] = "semantic-router-ko-v10"  # 관계를 썼는데 -resolved 가 아님
    s = summarize({"resolver": {"confidence_floor": 0.6}, "label": "t"}, rows)
    assert s["accuracy"]["point"] == pytest.approx(2 / 3, abs=1e-3)
    assert s["confusion"]["FOLLOW_UP"] == {"FOLLOW_UP": 1, "FAST_NEW": 1}
    assert s["strata"]["followup_no_marker"]["correct"] == 0
    assert s["wiring_mismatch"] == 1
    assert s["misses"][0]["got"] == "FAST_NEW"


def test_script_model_rejects_unknown_relation() -> None:
    with pytest.raises(ValueError):
        Script(
            script_id="tr_a_01",
            stratum="meta",
            prior=[{"user": "a", "assistant": "b"}],
            current="c",
            expect="SOMETHING",
            author="t",
        )
