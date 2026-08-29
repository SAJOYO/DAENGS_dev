"""크롤 실행 이력 — 기록 계약과 태스크 배선 (RAG-001 원칙 3 · RAG-047).

**이 파일이 지키는 가장 중요한 하나는 "기록이 크롤을 죽이지 않는다"** 이다. DB 가 없다고
수집이 멈추면 `crawl_due` 가 가진 성질(한 소스가 죽어도 나머지는 받는다, RAG-001 원칙 5의 절반)이
사라진다. 그래서 DB 없이 도는 검사를 먼저 두고, 연결이 필요한 것은 없으면 skip 한다.

**쓰고 나면 지운다.** DB 는 팀에 하나뿐이라(CLAUDE.md) 테스트가 흔적을 남기면 관리자 화면에
가짜 실행이 뜬다 — RAG-045 ③ 이 `documents` 에서 겪은 그 일이다. 여기서는 `crawl_runs` 가
append-only 라 롤백 대신 **teardown 에서 자기 행만 지운다.**
"""
from __future__ import annotations

import pytest

from daengs_life.tasks import crawl, crawl_runs

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

SRC = "__test__crawl-runs"           # 실물 소스 id 와 겹치지 않는 이름


@pytest.fixture
def db_or_skip():
    """연결이 되면 열어 주고, 끝나면 이 테스트가 만든 행을 지운다."""
    try:
        conn = crawl_runs._connect()
    except Exception as e:                      # noqa: BLE001
        pytest.skip(f"DB 없음 — {type(e).__name__}")
    try:
        yield conn
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM crawl_runs WHERE source_id LIKE %s", (f"{SRC}%",))
        conn.commit()
        conn.close()


def _rows(conn, source_id: str = SRC) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT status, run_id, docs_fetched, docs_changed, docs_failed, docs_skipped, "
            "changed_slugs, error, finished_at FROM crawl_runs "
            "WHERE source_id = %s ORDER BY id", (source_id,))
        return cur.fetchall()


# ---------------------------------------------------------------- 계약: 죽지 않는다
def test_start_returns_none_when_db_is_down(monkeypatch) -> None:
    """DB 가 없으면 `start()` 는 예외가 아니라 `None` 이다 — 수집은 계속돼야 한다."""
    monkeypatch.setattr(crawl_runs, "_connect", lambda: (_ for _ in ()).throw(OSError("no db")))
    assert crawl_runs.start("any", "due") is None


def test_finish_on_none_is_a_no_op(monkeypatch) -> None:
    """`start()` 가 실패한 뒤의 `finish(None, ...)` 은 연결조차 열지 않는다."""
    def _boom():
        raise AssertionError("연결을 열면 안 된다")
    monkeypatch.setattr(crawl_runs, "_connect", _boom)
    crawl_runs.finish(None, "ok")               # 예외가 안 나면 통과


def test_finish_swallows_db_failure(monkeypatch) -> None:
    """끝낼 때 DB 가 죽어도 태스크는 계속된다 — 결과는 로그에 남아 있다."""
    monkeypatch.setattr(crawl_runs, "_connect", lambda: (_ for _ in ()).throw(OSError("no db")))
    crawl_runs.finish(1, "ok", run_id="x")      # 예외가 안 나면 통과


# ---------------------------------------------------------------- 기록되는 값
def test_start_leaves_a_running_row_without_run_id(db_or_skip) -> None:
    """시작 시점에는 `run_id` 가 없다 — 수집이 끝나야 나온다.

    **워커가 중간에 죽으면 이 행이 `running` 으로 남는다. 그것이 정보다** — 지금까지는
    그렇게 죽으면 아무 흔적도 없었다.
    """
    row_id = crawl_runs.start(SRC, "due")
    assert row_id is not None
    (status, run_id, *_rest, finished_at), = _rows(db_or_skip)
    assert (status, run_id, finished_at) == ("running", None, None)


def test_finish_fills_counts_and_closes_the_row(db_or_skip) -> None:
    row_id = crawl_runs.start(SRC, "manual")
    crawl_runs.finish(row_id, "ok", run_id="20260830-010203",
                      counts={"fetched": 5, "changed": 2, "failed": 0, "skipped": 3},
                      changed_slugs=["a", "b"])
    (status, run_id, fetched, changed, failed, skipped, slugs, error, finished_at), = _rows(db_or_skip)
    assert status == "ok" and run_id == "20260830-010203"
    assert (fetched, changed, failed, skipped) == (5, 2, 0, 3)
    assert slugs == ["a", "b"] and error is None and finished_at is not None


def test_unavailable_is_not_failed(db_or_skip) -> None:
    """키 미설정·시드 URL 사망은 **실패가 아니라 사람이 고쳐야 하는 것**이라 상태를 가른다."""
    crawl_runs.finish(crawl_runs.start(SRC, "due"), "unavailable", error="LAW_OC 가 없다")
    (status, _run_id, *_rest, error, _fin), = _rows(db_or_skip)
    assert status == "unavailable" and "LAW_OC" in error


# ---------------------------------------------------------------- 태스크 배선
class _FakeOutcome:
    def __init__(self, state: str, slug: str, changed: bool) -> None:
        self.state, self.slug, self.changed = state, slug, changed


def _fake_result(source_id: str):
    from daengs_life.crawler.run import RunResult
    return RunResult(source_id=source_id, run_id="20260830-999999",
                     outcomes=[_FakeOutcome("new", "doc-1", True),
                               _FakeOutcome("same", "doc-2", False)])


def test_crawl_due_records_each_source(db_or_skip, monkeypatch) -> None:
    """수동 트리거 한 번이 소스마다 한 행을 남긴다 — 행 단위가 '태스크'가 아니라 '소스'다."""
    monkeypatch.setattr(crawl.crawler_run, "run", _fake_result)
    out = crawl.crawl_due(source_ids=[SRC])
    assert out["mode"] == "manual"
    (status, run_id, fetched, changed, *_rest), = _rows(db_or_skip)
    assert (status, run_id) == ("ok", "20260830-999999")
    assert (fetched, changed) == (2, 1)


def test_crawl_due_records_a_failed_source(db_or_skip, monkeypatch) -> None:
    """한 소스가 죽어도 나머지는 받는다(원칙 5의 절반) — 그리고 죽은 것도 남는다."""
    def _boom(source_id: str):
        raise RuntimeError("사이트가 죽었다")
    monkeypatch.setattr(crawl.crawler_run, "run", _boom)
    crawl.crawl_due(source_ids=[SRC])
    (status, _run_id, *_rest, error, finished_at), = _rows(db_or_skip)
    assert status == "failed"
    assert "사이트가 죽었다" in error and finished_at is not None


def test_recording_failure_does_not_stop_the_crawl(monkeypatch) -> None:
    """DB 가 통째로 죽어도 `crawl_due` 는 수집 결과를 정상으로 돌려준다.

    이것이 이 카드에서 제일 중요한 단언이다 — 이력을 붙이면서 **크롤이 DB 에 의존하게 되는
    것**이 가장 흔한 사고다.
    """
    monkeypatch.setattr(crawl_runs, "_connect", lambda: (_ for _ in ()).throw(OSError("no db")))
    monkeypatch.setattr(crawl.crawler_run, "run", _fake_result)
    out = crawl.crawl_due(source_ids=[SRC])
    assert out["results"][SRC]["fetched"] == 2
    assert out["changed_docs"] == {SRC: ["doc-1"]}
