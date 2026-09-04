"""`python -m crawler due` — 코퍼스 이관 뒤 "로그가 같이 왔나"를 보는 자리 (RAG-050).

Beat 의 `crawl_due` 와 **같은 판정 함수**를 타야 한다. 여기서 due 0 인데 04:00 에 전부 받으면
이 명령이 거짓말을 한 것이다.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from daengs_life.crawler import __main__ as cli
from daengs_life.crawler.core import cadence, config, registry

NOW = datetime(2026, 8, 30, 4, 0, tzinfo=config.KST)


def seed(sid: str, *, domain: str = "subsidy", method: str = "html",
         status: str = "verified", **extra) -> dict:
    return {"id": sid, "domain": domain, "method": method, "status": status, **extra}


@pytest.fixture
def three_seeds(monkeypatch: pytest.MonkeyPatch):
    seeds = {
        "stale": seed("stale"),
        "fresh": seed("fresh"),
        "law-x": seed("law-x", domain="law"),        # cadence=manual → 후보가 아니다
    }
    monkeypatch.setattr(registry, "load_seeds", lambda: seeds)
    monkeypatch.setattr(registry, "resolve", lambda s: object())      # 전부 구현된 셈
    monkeypatch.setattr(cli, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: NOW)}))
    return seeds


def _log(tmp_path: Path, rows: list[dict]) -> Path:
    p = tmp_path / "crawl_log.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return p


def test_without_a_log_every_candidate_is_due_and_it_says_so(
        three_seeds, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(config, "CRAWL_LOG", tmp_path / "missing.jsonl")
    assert cli.main(["due"]) == 0
    out, err = capsys.readouterr()
    assert "crawl_log.jsonl 이 없다" in err
    assert "due 2 / 후보 2 / 시드 3" in out
    assert "[skip] law-x" in out and "cadence=manual" in out


def test_with_the_log_only_stale_sources_are_due(
        three_seeds, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """이관이 제대로 됐다는 증거가 이 모양이다 — due 가 후보 전체보다 작다."""
    log = _log(tmp_path, [
        {"source_id": "stale", "fetched_at": (NOW - timedelta(days=8)).isoformat(), "error": None},
        {"source_id": "fresh", "fetched_at": (NOW - timedelta(days=1)).isoformat(), "error": None},
    ])
    monkeypatch.setattr(config, "CRAWL_LOG", log)
    assert cli.main(["due"]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert "due 1 / 후보 2 / 시드 3" in out
    assert "[due]  stale" in out and "[    ] fresh" in out


def test_the_cli_agrees_with_the_beat_task(three_seeds, tmp_path: Path, monkeypatch) -> None:
    """같은 함수를 타는지를 계약으로 — 둘이 갈리면 이 명령으로 확인한 것이 무의미하다."""
    log = _log(tmp_path, [
        {"source_id": "fresh", "fetched_at": (NOW - timedelta(days=1)).isoformat(), "error": None},
    ])
    monkeypatch.setattr(config, "CRAWL_LOG", log)
    expected = cadence.due_sources(three_seeds, implemented=set(three_seeds), now=NOW)
    assert expected == ["stale"]
