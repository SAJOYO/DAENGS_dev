from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.repository_documents_test import SearchTestResult
from app.services.reranker import rerank


def _sr(content: str, score: float) -> SearchTestResult:
    return SearchTestResult(
        id=uuid4(),
        content=content,
        category="food-safety",
        subcategory="toxic-food",
        source=None,
        source_type=None,
        document_title=None,
        section=None,
        source_url=None,
        metadata={},
        score=score,
    )


def _fake_ollama_client(response_text: str):
    """chat()을 한 번 호출하면 response_text를 반환하는 가짜 클라이언트
    (배치 리랭크는 후보 전체를 한 번의 LLM 호출로 채점하므로 응답도 하나)."""
    mock_client = MagicMock()
    mock_client.chat.return_value = {"message": {"content": response_text}}
    return mock_client


@patch("app.services.reranker.ollama.Client")
def test_rerank_orders_by_llm_relevance_score(mock_client_cls):
    mock_client_cls.return_value = _fake_ollama_client("0: 2\n1: 9\n2: 5")
    candidates = [
        _sr("관련성 낮음", 0.5),
        _sr("관련성 높음", 0.5),
        _sr("중간", 0.5),
    ]

    result = rerank("질문", candidates, top_k=2)

    assert [r.content for r in result] == ["관련성 높음", "중간"]


@patch("app.services.reranker.ollama.Client")
def test_rerank_handles_unparseable_score_by_ranking_last(mock_client_cls):
    # 0번 후보는 형식을 안 지켜서 파싱이 안 되고, 1번만 정상적으로 채점됨.
    mock_client_cls.return_value = _fake_ollama_client("1: 7")
    candidates = [
        _sr("이상한 응답", 0.5),
        _sr("정상 응답", 0.5),
    ]

    result = rerank("질문", candidates, top_k=2)

    assert [r.content for r in result] == ["정상 응답", "이상한 응답"]


def test_rerank_empty_candidates_returns_empty():
    assert rerank("질문", [], top_k=3) == []
