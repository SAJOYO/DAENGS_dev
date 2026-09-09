"""적재 조립 — prepare → count → metadata_loss → guard → 한 트랜잭션(upsert + prune).
DB 는 가짜 연결로 바꾸고 **호출 순서와 트랜잭션 경계**만 본다."""
from contextlib import contextmanager
from types import SimpleNamespace

from daengs_life.jobs import load as jobs_load


class FakeConn:
    def __init__(self, before: int, after: int, stale_rows: list):
        self.calls: list[str] = []
        self._counts = iter([before, after])
        self._stale = stale_rows
        self.tx_depth = 0

    @contextmanager
    def transaction(self):
        self.tx_depth += 1
        self.calls.append(f"tx{self.tx_depth}")
        try:
            yield
        finally:
            self.tx_depth -= 1

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _wire(monkeypatch, *, before, after, losing, stale_rows, rows):
    conn = FakeConn(before, after, stale_rows)
    L = jobs_load.loader
    monkeypatch.setattr(L, "prepare", lambda key: SimpleNamespace(rows=rows, merged=0, model_repo="m"))
    monkeypatch.setattr(L, "connect", lambda: conn)
    monkeypatch.setattr(L, "count", lambda c: next(c._counts))
    monkeypatch.setattr(L, "metadata_loss", lambda c, r: losing)
    monkeypatch.setattr(L, "upsert", lambda c, r: c.calls.append(f"upsert{len(r)}"))
    monkeypatch.setattr(L, "stale", lambda c, r: c._stale)
    monkeypatch.setattr(L, "prune", lambda c, t: c.calls.append(f"prune{len(t)}") or len(t))
    return conn


def test_happy_path_upserts_and_prunes_inside_one_transaction(monkeypatch):
    rows = [{"content_hash": "h1"}, {"content_hash": "h2"}]
    conn = _wire(monkeypatch, before=2, after=2, losing=[], stale_rows=[("h9", "old")], rows=rows)
    assert jobs_load.run_load(dry_run=False, max_drop=0.2) == 0
    assert conn.calls == ["tx1", "upsert2", "prune1"]


def test_guard_blocks_before_any_write(monkeypatch):
    rows = [{"content_hash": "h1"}]
    conn = _wire(monkeypatch, before=10, after=10, losing=[], stale_rows=[], rows=rows)
    assert jobs_load.run_load(dry_run=False, max_drop=0.2) == 1
    assert conn.calls == []


def test_metadata_loss_blocks(monkeypatch):
    rows = [{"content_hash": "h1"}] * 10
    conn = _wire(monkeypatch, before=10, after=10, losing=[("org", 5, 0)], stale_rows=[], rows=rows)
    assert jobs_load.run_load(dry_run=False, max_drop=0.2) == 1
    assert conn.calls == []


def test_dry_run_never_connects(monkeypatch):
    rows = [{"content_hash": "h1"}]
    monkeypatch.setattr(jobs_load.loader, "prepare",
                        lambda key: SimpleNamespace(rows=rows, merged=0, model_repo="m"))
    monkeypatch.setattr(jobs_load.loader, "connect",
                        lambda: (_ for _ in ()).throw(AssertionError("dry-run 은 DB 를 안 연다")))
    assert jobs_load.run_load(dry_run=True, max_drop=0.2) == 0


def test_prepare_failure_is_exit_1(monkeypatch):
    def boom(key):
        raise FileNotFoundError("parquet 없음")
    monkeypatch.setattr(jobs_load.loader, "prepare", boom)
    assert jobs_load.run_load(dry_run=False, max_drop=0.2) == 1
