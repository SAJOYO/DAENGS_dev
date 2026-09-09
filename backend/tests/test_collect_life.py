"""collect_life — 라우터 없이 Life 어댑터를 직접 부르는 수집기 (#343). 가짜 ask 로만 잰다."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

from daengs_backend.orchestration.adapters.life import LifeCapabilityAdapter
from daengs_evals.answer_quality import collect_life
from daengs_evals.answer_quality.questions import QuestionCase, write_questions
from daengs_evals.answer_quality.strata import STRATA_BY_ID


def _case(stratum: str, n: int) -> QuestionCase:
    s = STRATA_BY_ID[stratum]
    return QuestionCase(question_id=f"{stratum}_{n:02d}", query=f"질문 {n}", context=s.context(), stratum=stratum, generator_version="t")


def _ask_by_query(question: str, **_: object) -> dict:
    if "거절" in question:
        raise HTTPException(status_code=422, detail={"code": "medical_boundary", "message": "수의사에게", "hits": []})
    if "기권" in question:
        raise HTTPException(status_code=404, detail={"code": "no_evidence", "message": "근거 없음"})
    if "폭발" in question:
        raise RuntimeError("boom")
    # LifeCapabilityAdapter.run() 이 `upstream.answer`/`.hits`/`.cited`/`.ungrounded` 를 속성으로
    # 읽는다 (adapters/life.py:289-297, test_orchestration_adapters.py 의 `life_output()` 과 같은
    # 관례) — 평범한 dict 를 주면 OK 경로가 AttributeError 로 죽는다.
    return SimpleNamespace(answer=f"답: {question}", hits=[], cited=[], ungrounded=[])


def test_rows_carry_life_result_in_collect_shape_for_ok_refused_abstained_and_error() -> None:
    cases = [_case("life_food__polite", 1), _case("life_boundary__polite", 1), _case("life_travel__casual", 1), _case("life_policy__polite", 1)]
    cases[1] = cases[1].model_copy(update={"query": "거절 질문"})
    cases[2] = cases[2].model_copy(update={"query": "기권 질문"})
    cases[3] = cases[3].model_copy(update={"query": "폭발 질문"})
    adapter = LifeCapabilityAdapter(ask=_ask_by_query)
    rows = asyncio.run(collect_life.collect(cases, adapter=adapter, log=lambda _: None))
    assert [r["question_id"] for r in rows] == [c.question_id for c in cases]
    by = {r["question_id"]: r for r in rows}
    ok = by["life_food__polite_01"]
    assert ok["status"] == "ANSWERED" and ok["message"] == "답: 질문 1"
    assert ok["results"][0]["capability"] == "life" and ok["results"][0]["status"] == "OK"
    refused = by["life_boundary__polite_01"]
    assert refused["status"] == "REFUSED" and refused["results"][0]["refusal"]["code"] == "medical_boundary"
    assert refused["message"] == "수의사에게"
    abstained = by["life_travel__casual_01"]
    assert abstained["status"] == "FAILED" and abstained["results"][0]["status"] == "ABSTAINED"
    assert abstained["results"][0]["abstention"]["code"] == "no_evidence"
    failed = by["life_policy__polite_01"]
    # 어댑터가 예외를 ERROR 로 번역하면 results 에 ERROR 가, 번역 못 하면 error 칸에 문자열이 남는다 — 어느 쪽이든 수집은 계속된다.
    assert failed["status"] == "FAILED"
    assert (failed["results"] and failed["results"][0]["status"] == "ERROR") or failed["error"]
    assert all(set(r) >= {"kind", "question_id", "stratum", "status", "message", "results", "handoffs", "clarify", "plan", "error", "latency_ms"} for r in rows)


def test_main_writes_meta_and_rows_with_fake_ask(tmp_path: Path) -> None:
    qpath = tmp_path / "q.jsonl"
    write_questions(qpath, [_case("life_food__polite", 1), _case("life_food__polite", 2)])
    out = tmp_path / "answers_smoke_direct.jsonl"
    code = collect_life.main(["--questions", str(qpath), "--label", "smoke_direct", "--fake", "--out", str(out), "--limit", "1"])
    assert code == 0
    lines = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["kind"] == "meta" and lines[0]["adapters"] == "life-direct" and lines[0]["label"] == "smoke_direct"
    assert lines[0]["question_count"] == 1 and lines[0]["requested_count"] == 2 and lines[0]["questions_sha256"]
    assert len(lines) == 2 and lines[1]["kind"] == "answer" and lines[1]["results"][0]["capability"] == "life"
