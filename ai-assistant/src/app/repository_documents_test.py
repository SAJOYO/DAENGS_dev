# repository_documents_test.py = repository.py와 동일한 리포지토리 패턴이지만,
# 새 분류 체계(category/subcategory/source/source_type/document_title/section/source_url/metadata)를
# 쓰는 documents_test 테이블 전용 버전입니다.
#
# rag.py/hybrid_search.py/reranker.py가 이 모듈을 통해 documents_test를 조회합니다
# (옛 documents 테이블/repository.py는 /documents, /ingest 엔드포인트 전용으로 남아있음).

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from pgvector import Vector
from psycopg.types.json import Jsonb

from app.db import get_connection


@dataclass
class DocumentTestRecord:
    id: UUID
    content: str
    category: str
    subcategory: str
    source: str | None
    source_type: str | None
    document_title: str | None
    section: str | None
    source_url: str | None
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


@dataclass
class SearchTestResult:
    id: UUID
    content: str
    category: str
    subcategory: str
    source: str | None
    source_type: str | None
    document_title: str | None
    section: str | None
    source_url: str | None
    metadata: dict[str, Any]
    score: float


def insert_document_test(
    content: str,
    embedding: list[float],
    category: str,
    subcategory: str,
    metadata: dict[str, Any],
    source: str | None = None,
    source_type: str | None = None,
    document_title: str | None = None,
    section: str | None = None,
    source_url: str | None = None,
) -> DocumentTestRecord:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            INSERT INTO documents_test
                (content, embedding, category, subcategory, source, source_type,
                 document_title, section, source_url, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, content, category, subcategory, source, source_type,
                      document_title, section, source_url, metadata, created_at, updated_at
            """,
            (
                content,
                Vector(embedding),
                category,
                subcategory,
                source,
                source_type,
                document_title,
                section,
                source_url,
                Jsonb(metadata),
            ),
        ).fetchone()
    finally:
        conn.close()
    return DocumentTestRecord(
        id=row[0],
        content=row[1],
        category=row[2],
        subcategory=row[3],
        source=row[4],
        source_type=row[5],
        document_title=row[6],
        section=row[7],
        source_url=row[8],
        metadata=row[9],
        created_at=row[10],
        updated_at=row[11],
    )


def list_documents_test() -> list[DocumentTestRecord]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, content, category, subcategory, source, source_type,
                   document_title, section, source_url, metadata, created_at, updated_at
            FROM documents_test
            ORDER BY created_at DESC
            """
        ).fetchall()
    finally:
        conn.close()
    return [
        DocumentTestRecord(
            id=r[0],
            content=r[1],
            category=r[2],
            subcategory=r[3],
            source=r[4],
            source_type=r[5],
            document_title=r[6],
            section=r[7],
            source_url=r[8],
            metadata=r[9],
            created_at=r[10],
            updated_at=r[11],
        )
        for r in rows
    ]


def search_similar_test(query_embedding: list[float], top_k: int) -> list[SearchTestResult]:
    conn = get_connection()
    try:
        # category != 'community-signal' : 유튜브 댓글처럼 검증되지 않은 자료는 /chat 근거로
        # 쓰지 않는다 (사람이 검증한 사실만 답변 근거가 되어야 함 — BR2/BR3와 같은 취지).
        rows = conn.execute(
            """
            SELECT id, content, category, subcategory, source, source_type,
                   document_title, section, source_url, metadata,
                   1 - (embedding <=> %s) AS similarity
            FROM documents_test
            WHERE category != 'community-signal'
            ORDER BY embedding <=> %s
            LIMIT %s
            """,
            (Vector(query_embedding), Vector(query_embedding), top_k),
        ).fetchall()
    finally:
        conn.close()
    return [
        SearchTestResult(
            id=r[0],
            content=r[1],
            category=r[2],
            subcategory=r[3],
            source=r[4],
            source_type=r[5],
            document_title=r[6],
            section=r[7],
            source_url=r[8],
            metadata=r[9],
            score=float(r[10]),
        )
        for r in rows
    ]


# 긴 조사부터 검사해야 "이며"를 "며"로 잘못 자르는 대신 "이며"를 통째로 먼저 뗄 수 있음.
_JOSA_SUFFIXES = sorted(
    [
        "으로부터", "에서부터", "이라도", "이나마", "조차도",
        "에서", "으로", "까지", "부터", "한테", "에게", "이나", "이며", "이고", "이라",
        "은", "는", "이", "가", "을", "를", "의", "에", "와", "과", "도", "만", "랑", "나", "며", "고",
    ],
    key=len,
    reverse=True,
)


def _strip_josa(word: str) -> str:
    """단어 끝의 흔한 한국어 조사를 하나 떼어낸 어근을 반환. 어근이 1글자로 줄어들면
    (너무 광범위한 접두어 매칭이 될 수 있어) 원래 단어를 그대로 반환."""
    for suffix in _JOSA_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 2:
            return word[: -len(suffix)]
    return word


def _build_prefix_tsquery(query: str) -> str:
    """repository.py의 동일 함수와 같은 접두어 매칭 방식 (한국어 조사 문제 완화용).

    검색어 자체에 붙은 조사도 _strip_josa로 먼저 떼어낸 뒤 접두어를 만든다 — 안 그러면
    "등록을:*"가 "등록대상동물의" 같은 다른 조사/복합어 토큰과 전혀 안 맞는 문제가 있었음
    (law_qa_hit_rate 평가에서 keyword Hit@1 2.78%로 발견).
    """
    words = re.findall(r"[\w가-힣]+", query)
    return " | ".join(f"{_strip_josa(word)}:*" for word in words)


def search_keyword_test(query: str, top_k: int) -> list[SearchTestResult]:
    tsquery_str = _build_prefix_tsquery(query)
    if not tsquery_str:
        return []

    conn = get_connection()
    try:
        # search_similar_test와 동일한 이유로 community-signal 제외.
        rows = conn.execute(
            """
            SELECT id, content, category, subcategory, source, source_type,
                   document_title, section, source_url, metadata,
                   ts_rank_cd(content_tsv, to_tsquery('simple', %s)) AS rank
            FROM documents_test
            WHERE content_tsv @@ to_tsquery('simple', %s) AND category != 'community-signal'
            ORDER BY rank DESC
            LIMIT %s
            """,
            (tsquery_str, tsquery_str, top_k),
        ).fetchall()
    finally:
        conn.close()
    return [
        SearchTestResult(
            id=r[0],
            content=r[1],
            category=r[2],
            subcategory=r[3],
            source=r[4],
            source_type=r[5],
            document_title=r[6],
            section=r[7],
            source_url=r[8],
            metadata=r[9],
            score=float(r[10]),
        )
        for r in rows
    ]


def delete_document_test(document_id: UUID) -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM documents_test WHERE id = %s", (document_id,))
    finally:
        conn.close()
