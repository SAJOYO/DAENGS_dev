"""진입점 — 인자를 단계 호출로 바꾸고, 실패한 단계에서 멈추는지."""
from daengs_life.jobs import corpus_refresh as cr
from daengs_life.jobs import stages


def _wire(monkeypatch, calls, *, fail_at=None):
    monkeypatch.setattr(cr.lock, "from_env", lambda: None)

    def rec(name, rc=0):
        def f(*a, **kw):
            calls.append((name, kw))
            if name == fail_at:
                return 1
            return rc
        return f

    monkeypatch.setattr(cr.stages, "run_crawl",
                        lambda sources, *, dry_run: calls.append(("crawl", {"sources": sources, "dry_run": dry_run}))
                        or stages.CrawlSummary(selected=list(sources or []), ok=1))
    monkeypatch.setattr(cr.stages, "run_parse", rec("parse"))
    monkeypatch.setattr(cr.stages, "run_chunk", rec("chunk"))
    monkeypatch.setattr(cr.stages, "run_embed", rec("embed"))
    monkeypatch.setattr(cr.jobs_load, "run_load", rec("load"))


def test_default_runs_all_five_in_order(monkeypatch):
    calls = []
    _wire(monkeypatch, calls)
    assert cr.main([]) == 0
    assert [c[0] for c in calls] == ["crawl", "parse", "chunk", "embed", "load"]


def test_stops_at_first_failing_stage(monkeypatch):
    calls = []
    _wire(monkeypatch, calls, fail_at="chunk")
    assert cr.main([]) == 1
    assert [c[0] for c in calls] == ["crawl", "parse", "chunk"]


def test_flags_reach_the_stages(monkeypatch):
    calls = []
    _wire(monkeypatch, calls)
    assert cr.main(["--stages", "embed,load", "--full", "--dry-run", "--max-drop", "0.5"]) == 0
    assert calls == [("embed", {"full": True, "dry_run": True}),
                     ("load", {"dry_run": True, "max_drop": 0.5})]


def test_stages_accepts_space_separated_tokens(monkeypatch):
    calls = []
    _wire(monkeypatch, calls)
    assert cr.main(["--stages", "parse", "chunk"]) == 0
    assert [c[0] for c in calls] == ["parse", "chunk"]


def test_sources_accepts_space_separated_tokens(monkeypatch):
    calls = []
    _wire(monkeypatch, calls)
    cr.main(["--stages", "crawl", "--sources", "a", "b"])
    assert calls == [("crawl", {"sources": ["a", "b"], "dry_run": False})]


def test_sources_go_to_crawl_only(monkeypatch):
    calls = []
    _wire(monkeypatch, calls)
    cr.main(["--stages", "crawl", "--sources", "a, b"])
    assert calls == [("crawl", {"sources": ["a", "b"], "dry_run": False})]


def test_all_sources_failed_stops_pipeline(monkeypatch):
    calls = []
    _wire(monkeypatch, calls)
    monkeypatch.setattr(cr.stages, "run_crawl",
                        lambda sources, *, dry_run: stages.CrawlSummary(selected=["a"], failed=1))
    assert cr.main([]) == 1
    assert calls == []


def test_no_due_sources_is_not_a_failure(monkeypatch):
    calls = []
    _wire(monkeypatch, calls)
    monkeypatch.setattr(cr.stages, "run_crawl", lambda sources, *, dry_run: stages.CrawlSummary())
    assert cr.main(["--stages", "crawl"]) == 0


def test_skips_when_another_execution_is_running(monkeypatch):
    calls = []
    _wire(monkeypatch, calls)
    monkeypatch.setattr(cr.lock, "from_env", lambda: "corpus-refresh-xyz")
    assert cr.main([]) == 0
    assert calls == []


def test_seeds_are_copied_into_data_dir(monkeypatch, tmp_path):
    calls = []
    _wire(monkeypatch, calls)
    src = tmp_path / "seed_sources.yaml"
    src.write_text("- id: a\n", encoding="utf-8")
    data = tmp_path / "data"
    monkeypatch.setattr(cr, "_manifest_dir", lambda: data / "manifests")
    cr.main(["--stages", "parse", "--seeds-from", str(src)])
    assert (data / "manifests" / "seed_sources.yaml").read_text(encoding="utf-8") == "- id: a\n"


def test_lock_check_failure_does_not_stop_the_job(monkeypatch):
    calls = []
    _wire(monkeypatch, calls)

    def boom():
        raise RuntimeError("403 permission denied")

    monkeypatch.setattr(cr.lock, "from_env", boom)
    assert cr.main(["--stages", "parse"]) == 0
    assert [c[0] for c in calls] == ["parse"]
