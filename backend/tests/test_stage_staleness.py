"""단계가 낡은 산출물을 알아채는가 (RAG-066 · #270).

2026-09-06 에 지역 필터가 통째로 죽었다. 사고의 모양이 셋으로 갈린다:

  ① `rag chunk` 가 **청커 판이 올라간 것**을 못 알아채 08-29 자 청크가 그대로 남았다
  ② `rag parse` 도 같은 병이다 — 파서를 고쳐도 원본 해시가 같아 전부 건너뛴다
  ③ `rag load` 가 그 낡은 청크로 DB 를 덮어써 `org` 2,592행을 지웠고 **에러가 안 났다**

셋 다 **조용한 실패**라, 여기서 시끄럽게 만드는 것이 이 테스트의 일이다.
네트워크도 DB 도 `data/` 도 쓰지 않는다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from daengs_life.rag.core import io
from daengs_life.rag.core.io import RawDoc
from daengs_life.rag.stages import chunk as chunker
from daengs_life.rag.stages import load

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# ------------------------------------------------------------------ ① 청킹

def _write(path: Path, header: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(header, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


@pytest.fixture
def staged(tmp_path, monkeypatch):
    """parsed 1건 + 그것으로 만든 chunks 1건. 헤더만 있으면 판정에는 충분하다."""
    parsed = tmp_path / "parsed" / "doc__20260906.jsonl"
    chunks = tmp_path / "chunks" / "doc__20260906.jsonl"
    _write(parsed, {"type": "document", "doc_id": "doc__20260906"})
    _write(chunks, {"type": "chunkset", "doc_id": "doc__20260906",
                    "parsed_sha256": io.sha256_file(parsed), "chunker_version": 1})
    monkeypatch.setattr(io, "chunk_path", lambda stem: chunks)
    return parsed


def test_a_fresh_chunk_file_is_current(staged) -> None:
    assert io.is_chunk_current(staged, 1)


def test_a_bumped_chunker_makes_every_file_stale(staged) -> None:
    """**이 한 줄이 사고를 막는다.** `org` 을 싣도록 청커를 고친 RAG-063 이 판을 안 올려
    08-29 자 청크 245건이 `org` 없이 남았고, 그것이 DB 를 덮어썼다.
    """
    assert not io.is_chunk_current(staged, 2)


def test_without_a_version_the_old_behaviour_is_kept(staged) -> None:
    """판을 안 주면 해시만 본다 — 옛 호출부가 그대로 돈다."""
    assert io.is_chunk_current(staged)
    assert io.is_chunk_current(staged, None)


def test_a_changed_parsed_file_is_stale_regardless_of_version(staged) -> None:
    staged.write_text(staged.read_text(encoding="utf-8") + '{"type":"para"}\n',
                      encoding="utf-8", newline="\n")
    assert not io.is_chunk_current(staged, 1)


def test_the_chunker_version_is_actually_bumped() -> None:
    """`org` 을 싣기 시작한 판이 2다. 1로 되돌리면 낡은 청크가 다시 통과한다."""
    assert chunker.VERSION >= 2


# ------------------------------------------------------------------ ② 파싱

@pytest.fixture
def parsed_only(tmp_path, monkeypatch):
    path = tmp_path / "parsed" / "doc__20260906.jsonl"
    _write(path, {"type": "document", "doc_id": "doc__20260906",
                  "raw_sha256": "abc", "parser_version": 1})
    monkeypatch.setattr(io, "parsed_path", lambda doc_id: path)
    return RawDoc(meta={"sha256": "abc"}, path=Path("raw/x/doc__20260906.html"),
                  meta_path=Path("raw/x/doc__20260906.meta.json"))


def test_a_bumped_parser_makes_its_documents_stale(parsed_only) -> None:
    """#268 이 `nias_pet` 파서를 v2 로 올리고 **손으로 `--force` 를 붙여야 했다.**
    붙이는 것을 잊으면 옛 파싱이 조용히 남는다 — 기억에 기대지 않게 한다.
    """
    assert io.is_current(parsed_only, 1)
    assert not io.is_current(parsed_only, 2)


def test_a_source_without_a_parser_version_is_judged_by_hash_alone(parsed_only) -> None:
    assert io.is_current(parsed_only, None)


# ------------------------------------------------------------------ ③ 적재 가드

class _Cursor:
    def __init__(self, rows): self.rows = rows
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=None): self._last = sql
    def fetchall(self): return self.rows


class _Conn:
    def __init__(self, rows): self.rows = rows
    def cursor(self): return _Cursor(self.rows)


def _rows(*metas):
    return [{"metadata": m} for m in metas]


def test_a_key_that_would_vanish_is_reported() -> None:
    """실제로 난 사고 그대로 — DB 에 `org` 이 2,592행 있는데 이번 청크에는 하나도 없다."""
    conn = _Conn([("chunk_id", 9836), ("org", 2592)])
    assert load.metadata_loss(conn, _rows({"chunk_id": "a"}, {"chunk_id": "b"})) == [
        ("org", 2592, 0)]


def test_a_key_that_survives_is_not_reported() -> None:
    conn = _Conn([("chunk_id", 9836), ("org", 2592)])
    assert load.metadata_loss(conn, _rows({"chunk_id": "a", "org": "부산광역시 동래구"})) == []


def test_a_partial_drop_is_allowed() -> None:
    """일부가 줄어드는 것은 정상일 수 있다(소스를 뺐다거나). **통째로 사라지는 것만** 잡는다 —
    규칙이 좁아야 사람이 `--allow-metadata-loss` 를 반사적으로 붙이지 않는다.
    """
    conn = _Conn([("org", 2592)])
    assert load.metadata_loss(conn, _rows({"org": "x"}, {}, {}, {})) == []


def test_a_key_the_loader_does_not_know_is_still_caught() -> None:
    """마이그레이션이 넣은 키는 정의상 `META_FIELDS` 밖일 수 있다 — **그런 키야말로** 잡혀야 한다.
    `org` 이 실제로 그렇게 살고 있었다.
    """
    conn = _Conn([("backfilled_by_migration", 245)])
    assert load.metadata_loss(conn, _rows({"chunk_id": "a"})) == [
        ("backfilled_by_migration", 245, 0)]


def test_an_empty_database_reports_nothing() -> None:
    """첫 적재를 막으면 안 된다."""
    assert load.metadata_loss(_Conn([]), _rows({"chunk_id": "a"})) == []


def test_org_is_a_metadata_field_the_loader_carries() -> None:
    """`META_FIELDS` 에서 빠지면 청크에 `org` 이 있어도 DB 로 안 간다 (RAG-063)."""
    assert "org" in load.META_FIELDS
