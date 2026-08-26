import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.repository import DocumentRecord
from app.services.rag import ChatResult, Source

client = TestClient(app)


def test_health():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@patch("app.routers.chat.answer_query")
def test_chat_grounded(mock_answer_query):
    mock_answer_query.return_value = ChatResult(
        answer="포도는 위험합니다.",
        sources=[Source(content="포도는 위험합니다.", score=0.9)],
        grounded=True,
    )

    response = client.post("/chat", json={"query": "포도 먹여도 돼?"})

    assert response.status_code == 200
    body = response.json()
    assert body["grounded"] is True
    assert len(body["sources"]) == 1
    assert "병원" not in body["answer"]


@patch("app.routers.chat.answer_query")
def test_chat_ungrounded(mock_answer_query):
    mock_answer_query.return_value = ChatResult(
        answer="정확히 알 수 없습니다.", sources=[], grounded=False
    )

    response = client.post("/chat", json={"query": "전혀 관련 없는 질문"})

    assert response.status_code == 200
    body = response.json()
    assert body["grounded"] is False
    assert body["sources"] == []


@patch("app.routers.chat.answer_query", side_effect=RuntimeError("ollama down"))
def test_chat_service_unavailable(mock_answer_query):
    response = client.post("/chat", json={"query": "아무 질문"})

    assert response.status_code == 503


def test_chat_rejects_empty_query():
    response = client.post("/chat", json={"query": ""})

    assert response.status_code == 422


def test_chat_rejects_non_korean_query():
    response = client.post("/chat", json={"query": "What should I feed my dog?"})

    assert response.status_code == 422


@patch("app.routers.documents.embed_text")
@patch("app.routers.documents.insert_document")
def test_create_document(mock_insert, mock_embed):
    mock_embed.return_value = [0.1, 0.2]
    mock_insert.return_value = DocumentRecord(
        id=uuid.uuid4(),
        content="테스트 문서",
        source_tag="test",
        created_at=datetime.now(timezone.utc),
    )

    response = client.post("/documents", json={"content": "테스트 문서", "source_tag": "test"})

    assert response.status_code == 201
    assert response.json()["content"] == "테스트 문서"


@patch("app.routers.documents.list_documents")
def test_get_documents(mock_list):
    mock_list.return_value = [
        DocumentRecord(
            id=uuid.uuid4(), content="문서1", source_tag=None, created_at=datetime.now(timezone.utc)
        )
    ]

    response = client.get("/documents")

    assert response.status_code == 200
    assert len(response.json()) == 1


@patch("app.routers.ingest.ingest_url")
def test_ingest_url_creates_documents(mock_ingest_url):
    mock_ingest_url.return_value = [
        DocumentRecord(
            id=uuid.uuid4(),
            content="수집된 청크",
            source_tag="test",
            source_url="https://example.com/dog-care",
            created_at=datetime.now(timezone.utc),
        )
    ]

    response = client.post("/ingest", json={"url": "https://example.com/dog-care", "source_tag": "test"})

    assert response.status_code == 201
    body = response.json()
    assert body["chunks_created"] == 1
    assert body["documents"][0]["source_url"] == "https://example.com/dog-care"


@patch("app.routers.ingest.ingest_url", side_effect=RuntimeError("fetch failed"))
def test_ingest_url_reports_failure(mock_ingest_url):
    response = client.post("/ingest", json={"url": "https://not-a-real-site.invalid"})

    assert response.status_code == 502
