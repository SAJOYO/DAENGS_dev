import uuid
from datetime import datetime
from unittest.mock import MagicMock, patch

from app.repository_documents_test import (
    DocumentTestRecord,
    insert_document_test,
    search_keyword_test,
    search_similar_test,
)


def _make_conn(fetchone=None, fetchall=None):
    conn = MagicMock()
    execute_result = MagicMock()
    execute_result.fetchone.return_value = fetchone
    execute_result.fetchall.return_value = fetchall or []
    conn.execute.return_value = execute_result
    return conn


@patch("app.repository_documents_test.get_connection")
def test_insert_document_test_returns_record(mock_get_connection):
    doc_id = uuid.uuid4()
    now = datetime.now()
    mock_get_connection.return_value = _make_conn(
        fetchone=(
            doc_id,
            "강아지는 하루 2끼가 권장됩니다.",
            "nutrition-guideline",
            "feeding-schedule",
            "VCA Animal Hospitals",
            "animal-hospital",
            "Nutrition - General Feeding Guidelines for Dogs",
            None,
            "https://vcahospitals.com/know-your-pet/nutrition-general-feeding-guidelines-for-dogs",
            {"source_url": "https://vcahospitals.com/know-your-pet/nutrition-general-feeding-guidelines-for-dogs"},
            now,
            now,
        )
    )

    record = insert_document_test(
        content="강아지는 하루 2끼가 권장됩니다.",
        embedding=[0.1, 0.2],
        category="nutrition-guideline",
        subcategory="feeding-schedule",
        metadata={"source_url": "https://vcahospitals.com/know-your-pet/nutrition-general-feeding-guidelines-for-dogs"},
        source="VCA Animal Hospitals",
        source_type="animal-hospital",
        document_title="Nutrition - General Feeding Guidelines for Dogs",
        source_url="https://vcahospitals.com/know-your-pet/nutrition-general-feeding-guidelines-for-dogs",
    )

    assert isinstance(record, DocumentTestRecord)
    assert record.id == doc_id
    assert record.category == "nutrition-guideline"
    assert record.subcategory == "feeding-schedule"
    assert record.source_url.startswith("https://vcahospitals.com")
    assert record.metadata["source_url"].startswith("https://vcahospitals.com")


@patch("app.repository_documents_test.get_connection")
def test_search_similar_test_returns_results_ordered_by_score(mock_get_connection):
    mock_get_connection.return_value = _make_conn(
        fetchall=[
            (
                uuid.uuid4(),
                "성견은 38종의 권장 영양소 기준이 있습니다.",
                "nutrition-guideline",
                "life-stage-nutrient-standard",
                "NIAS",
                "government-standard",
                "반려동물(개·고양이) 사료 영양표준",
                None,
                "https://www.korea.kr/news/policyNewsView.do?newsId=156656220",
                {"source_url": "https://www.korea.kr/news/policyNewsView.do?newsId=156656220"},
                0.87,
            )
        ]
    )

    results = search_similar_test(query_embedding=[0.1, 0.2], top_k=3)

    assert len(results) == 1
    assert results[0].category == "nutrition-guideline"
    assert results[0].source == "NIAS"
    assert results[0].score == 0.87


@patch("app.repository_documents_test.get_connection")
def test_search_keyword_test_builds_prefix_tsquery(mock_get_connection):
    mock_conn = _make_conn(fetchall=[])
    mock_get_connection.return_value = mock_conn

    search_keyword_test("자일리톨 위험", top_k=5)

    args, _ = mock_conn.execute.call_args
    tsquery_arg = args[1][0]
    assert "자일리톨:*" in tsquery_arg
    assert "위험:*" in tsquery_arg


@patch("app.repository_documents_test.get_connection")
def test_search_keyword_test_returns_empty_for_blank_query(mock_get_connection):
    results = search_keyword_test("   ", top_k=5)

    assert results == []
    mock_get_connection.assert_not_called()


@patch("app.repository_documents_test.get_connection")
def test_search_similar_test_excludes_community_signal(mock_get_connection):
    mock_conn = _make_conn(fetchall=[])
    mock_get_connection.return_value = mock_conn

    search_similar_test(query_embedding=[0.1, 0.2], top_k=5)

    args, _ = mock_conn.execute.call_args
    sql = args[0]
    assert "community-signal" in sql


@patch("app.repository_documents_test.get_connection")
def test_search_keyword_test_excludes_community_signal(mock_get_connection):
    mock_conn = _make_conn(fetchall=[])
    mock_get_connection.return_value = mock_conn

    search_keyword_test("자일리톨", top_k=5)

    args, _ = mock_conn.execute.call_args
    sql = args[0]
    assert "community-signal" in sql
