"""리포지토리 레이어 테스트.

PostgreSQL+pgvector 컨테이너가 실행 중이어야 합니다: `docker compose up -d db`
"""

import pytest

from app.repository import delete_document, insert_document, search_keyword, search_similar

EMBEDDING_DIM = 1024


def _fake_embedding(seed: float) -> list[float]:
    return [seed] * EMBEDDING_DIM


@pytest.fixture
def seeded_document():
    record = insert_document("테스트: 포도는 위험합니다.", _fake_embedding(0.9), "test")
    yield record
    delete_document(record.id)


def test_insert_and_search_similar(seeded_document):
    results = search_similar(_fake_embedding(0.9), top_k=1)

    assert len(results) >= 1
    assert any("포도" in r.content for r in results)


def test_search_similar_orders_by_similarity(seeded_document):
    close_match = insert_document("테스트: 포도 관련 유사 문서", _fake_embedding(0.89), "test")
    try:
        results = search_similar(_fake_embedding(0.9), top_k=2)
        assert len(results) == 2
        assert results[0].score >= results[1].score
    finally:
        delete_document(close_match.id)


def test_search_keyword_matches_exact_term():
    record = insert_document("테스트: 자일리톨은 강아지에게 위험합니다.", _fake_embedding(0.42), "test")
    try:
        results = search_keyword("자일리톨", top_k=5)
        assert any("자일리톨" in r.content for r in results)
    finally:
        delete_document(record.id)


def test_search_keyword_no_match_returns_empty():
    results = search_keyword("존재하지않는매우희귀한단어그자체", top_k=5)
    assert results == []
