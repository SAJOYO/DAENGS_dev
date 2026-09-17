"""`io.chunk_files()` 가 문서별 최신 날짜 파일만 돌려주는지 (RAG-087).

**왜** — 수집(`crawler/core/store.py`)은 옛 원본을 지우지 않고 `{slug}__{YYYYMMDD}` 로 새로
쓴다. parse·chunk 가 날짜별로 따로 산출물을 만들어, 옛 `chunk_files()` 는 그것을 전부
돌려주고 embed·load·`goldenset.corpus_index` 가 옛 판까지 읽었다. 실물 데이터 없이 도는
계약 테스트라 `tmp_path` 로 `config.CHUNK_DIR` 을 바꾼다 — `test_nias_food.py` 의
`_saved_meta` 가 `config.RAW_DIR`·`config.require_data_dir` 를 바꾸는 것과 같은 방식이다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from daengs_life.rag.core import config, io
from daengs_life.rag.stages import embed

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _touch(dir_: Path, *stems: str) -> None:
    for stem in stems:
        (dir_ / f"{stem}.jsonl").write_text("", encoding="utf-8")


@pytest.fixture(autouse=True)
def _chunk_dir(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(config, "CHUNK_DIR", tmp_path)
    monkeypatch.setattr(config, "require_data_dir", lambda: None)
    return tmp_path


def test_keeps_latest_date_per_document(_chunk_dir: Path) -> None:
    _touch(_chunk_dir, "law-a__20260820", "law-a__20260917", "law-b__20260820")
    got = [p.stem for p in io.chunk_files()]
    assert got == ["law-a__20260917", "law-b__20260820"]


def test_three_date_copies_keep_only_the_latest(_chunk_dir: Path) -> None:
    _touch(_chunk_dir, "law-a__20260701", "law-a__20260820", "law-a__20260917")
    got = [p.stem for p in io.chunk_files()]
    assert got == ["law-a__20260917"]


def test_file_without_date_suffix_stays(_chunk_dir: Path) -> None:
    _touch(_chunk_dir, "law-a__20260917", "srt-terms")
    got = [p.stem for p in io.chunk_files()]
    assert got == ["law-a__20260917", "srt-terms"]


def test_groups_by_last_double_underscore_only(_chunk_dir: Path) -> None:
    """slug 자체에 `__` 가 여럿 있어도 마지막 `__YYYYMMDD` 기준으로 묶인다."""
    _touch(_chunk_dir, "x__y__20260101", "x__y__20260201")
    got = [p.stem for p in io.chunk_files()]
    assert got == ["x__y__20260201"]


def _write_chunk_file(path: Path, chunk_id: str, content: str) -> None:
    rows = [
        {"type": "header", "doc_id": path.stem},
        {"type": "chunk", "chunk_id": chunk_id, "content": content},
    ]
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_embed_load_chunks_excludes_the_old_revision(_chunk_dir: Path) -> None:
    """같은 논리 주소, 다른 내용 — 새 판 content 만 나와야 한다."""
    _write_chunk_file(_chunk_dir / "law-a__20260820.jsonl", "law-a__20260820#제1조", "옛 내용")
    _write_chunk_file(_chunk_dir / "law-a__20260917.jsonl", "law-a__20260917#제1조", "새 내용")

    rows = embed.load_chunks()
    assert [r["content"] for r in rows] == ["새 내용"]
    assert [r["chunk_id"] for r in rows] == ["law-a__20260917#제1조"]
