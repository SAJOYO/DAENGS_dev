"""조립층 — 단계 순서·인자 파싱·크롤 요약. 실제 크롤·파싱은 가짜로 바꾼다."""
import argparse
from types import SimpleNamespace

import pytest

from daengs_life.jobs import stages


def test_default_is_every_stage_in_order():
    assert stages.parse_stages(None) == ["crawl", "parse", "chunk", "embed", "load"]


def test_subset_keeps_pipeline_order_regardless_of_input_order():
    assert stages.parse_stages("load,parse") == ["parse", "load"]


def test_unknown_stage_is_an_error():
    with pytest.raises(ValueError, match="모르는 단계"):
        stages.parse_stages("crawl,index")


def test_run_parse_passes_dry_run_to_the_cli_function(monkeypatch):
    seen = {}

    def fake_cmd_parse(args: argparse.Namespace) -> int:
        seen.update(vars(args))
        return 0

    monkeypatch.setattr(stages.rag_cli, "cmd_parse", fake_cmd_parse)
    assert stages.run_parse(dry_run=True) == 0
    assert seen == {"source": None, "limit": 0, "force": False, "dry_run": True, "verbose": False}


def test_run_embed_uses_serving_model_and_full_flag(monkeypatch):
    seen = {}
    monkeypatch.setattr(stages.rag_cli, "cmd_embed", lambda a: seen.update(vars(a)) or 0)
    stages.run_embed(full=True, dry_run=False)
    assert seen["model"] == stages.rag_config.settings.embedding_model_key
    assert seen["full"] is True and seen["all_models"] is False


def test_run_crawl_picks_due_sources_and_records_each(monkeypatch):
    monkeypatch.setattr(stages.registry, "load_seeds", lambda: {"a": {}, "b": {}, "c": {}})
    monkeypatch.setattr(stages.registry, "resolve", lambda seed: object())
    monkeypatch.setattr(stages.cadence, "due_sources", lambda seeds, implemented, now: ["a", "c"])
    started, finished = [], []
    monkeypatch.setattr(stages.crawl_runs, "start", lambda sid, trig: started.append((sid, trig)) or 1)
    monkeypatch.setattr(stages.crawl_runs, "finish", lambda rid, status, **kw: finished.append(status))
    results = {
        "a": SimpleNamespace(unavailable=None, fetched=3, changed=1, failed=0, skipped=2,
                             run_id="r1", changed_slugs=["x"]),
        "c": SimpleNamespace(unavailable="LAW_OC 미설정", fetched=0, changed=0, failed=0,
                             skipped=0, run_id=None, changed_slugs=[]),
    }
    monkeypatch.setattr(stages.crawler_run, "run", lambda sid, **kw: results[sid])

    s = stages.run_crawl(None, dry_run=False)

    assert s.selected == ["a", "c"]
    assert (s.ok, s.failed, s.unavailable) == (1, 0, 1)
    assert started == [("a", "due"), ("c", "due")]
    assert finished == ["ok", "unavailable"]


def test_run_crawl_manual_sources_skip_due_check(monkeypatch):
    monkeypatch.setattr(stages.registry, "load_seeds", lambda: {"a": {}})
    monkeypatch.setattr(stages.cadence, "due_sources",
                        lambda *a, **k: pytest.fail("수동 지정이면 due 판정을 안 탄다"))
    monkeypatch.setattr(stages.crawl_runs, "start", lambda sid, trig: 1)
    monkeypatch.setattr(stages.crawl_runs, "finish", lambda *a, **k: None)
    monkeypatch.setattr(stages.crawler_run, "run",
                        lambda sid, **kw: SimpleNamespace(unavailable=None, fetched=1, changed=0,
                                                          failed=0, skipped=0, run_id="r",
                                                          changed_slugs=[]))
    s = stages.run_crawl(["a"], dry_run=False)
    assert s.selected == ["a"] and s.ok == 1


def test_run_crawl_exception_in_one_source_is_recorded_and_others_continue(monkeypatch):
    monkeypatch.setattr(stages.registry, "load_seeds", lambda: {"a": {}, "b": {}})
    monkeypatch.setattr(stages.crawl_runs, "start", lambda sid, trig: 1)
    finished = []
    monkeypatch.setattr(stages.crawl_runs, "finish",
                        lambda rid, status, **kw: finished.append((status, kw.get("error"))))

    def boom_or_ok(sid, **kw):
        if sid == "a":
            raise RuntimeError("네트워크")
        return SimpleNamespace(unavailable=None, fetched=1, changed=0, failed=0, skipped=0,
                               run_id="r", changed_slugs=[])

    monkeypatch.setattr(stages.crawler_run, "run", boom_or_ok)
    s = stages.run_crawl(["a", "b"], dry_run=False)
    assert (s.ok, s.failed) == (1, 1)
    assert finished[0][0] == "failed" and "RuntimeError" in finished[0][1]
