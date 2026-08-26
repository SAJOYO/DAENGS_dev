from unittest.mock import patch
from uuid import uuid4

from app.repository_documents_test import SearchTestResult
from app.services.rag import answer_query


def _sr(content: str, score: float, source_url: str | None = None) -> SearchTestResult:
    return SearchTestResult(
        id=uuid4(),
        content=content,
        category="food-safety",
        subcategory="toxic-food",
        source=None,
        source_type=None,
        document_title=None,
        section=None,
        source_url=source_url,
        metadata={},
        score=score,
    )


@patch("app.services.rag.apply_guardrail", side_effect=lambda x: x)
@patch("app.services.rag.rerank")
@patch("app.services.rag.generate_answer")
@patch("app.services.rag.search_keyword_test")
@patch("app.services.rag.search_similar_test")
@patch("app.services.rag.embed_text")
def test_answer_query_grounded_path(
    mock_embed, mock_search, mock_keyword, mock_generate, mock_rerank, mock_guardrail
):
    mock_embed.return_value = [0.1, 0.2]
    mock_search.return_value = [_sr("포도는 위험합니다.", 0.9)]
    mock_keyword.return_value = []
    mock_rerank.side_effect = lambda query, candidates, top_k: candidates[:top_k]
    mock_generate.return_value = "포도는 위험합니다."

    result = answer_query("포도 먹여도 돼?")

    assert result.grounded is True
    assert len(result.sources) == 1
    mock_generate.assert_called_once_with("포도 먹여도 돼?", ["포도는 위험합니다."])


@patch("app.services.rag.apply_guardrail", side_effect=lambda x: x)
@patch("app.services.rag.rerank")
@patch("app.services.rag.generate_answer")
@patch("app.services.rag.search_keyword_test")
@patch("app.services.rag.search_similar_test")
@patch("app.services.rag.embed_text")
def test_answer_query_ungrounded_path_when_no_match(
    mock_embed, mock_search, mock_keyword, mock_generate, mock_rerank, mock_guardrail
):
    mock_embed.return_value = [0.1, 0.2]
    mock_search.return_value = []
    mock_generate.return_value = "정확히 알 수 없습니다."

    result = answer_query("전혀 관련 없는 질문")

    assert result.grounded is False
    assert result.sources == []
    mock_generate.assert_called_once_with("전혀 관련 없는 질문", [])
    # grounded가 애초에 False라, 키워드 검색/리랭킹까지 갈 필요가 없어야 함 (효율성 + 부수효과 없음 확인).
    mock_keyword.assert_not_called()
    mock_rerank.assert_not_called()


@patch("app.services.rag.apply_guardrail", side_effect=lambda x: x)
@patch("app.services.rag.rerank")
@patch("app.services.rag.generate_answer")
@patch("app.services.rag.search_keyword_test")
@patch("app.services.rag.search_similar_test")
@patch("app.services.rag.embed_text")
def test_answer_query_ungrounded_path_when_below_threshold(
    mock_embed, mock_search, mock_keyword, mock_generate, mock_rerank, mock_guardrail
):
    mock_embed.return_value = [0.1, 0.2]
    mock_search.return_value = [_sr("무관한 문서", 0.1)]
    mock_generate.return_value = "정확히 알 수 없습니다."

    result = answer_query("전혀 관련 없는 질문")

    assert result.grounded is False
    assert result.sources == []
    mock_keyword.assert_not_called()
    mock_rerank.assert_not_called()


@patch("app.services.rag.apply_guardrail")
@patch("app.services.rag.rerank")
@patch("app.services.rag.generate_answer")
@patch("app.services.rag.search_keyword_test")
@patch("app.services.rag.search_similar_test")
@patch("app.services.rag.embed_text")
def test_answer_query_applies_guardrail_to_final_answer(
    mock_embed, mock_search, mock_keyword, mock_generate, mock_rerank, mock_guardrail
):
    mock_embed.return_value = [0.1, 0.2]
    mock_search.return_value = [_sr("포도는 위험합니다.", 0.9)]
    mock_keyword.return_value = []
    mock_rerank.side_effect = lambda query, candidates, top_k: candidates[:top_k]
    mock_generate.return_value = "동물병원에 가보세요."
    mock_guardrail.return_value = "안전한 대체 문구입니다."

    result = answer_query("포도 먹여도 돼?")

    mock_guardrail.assert_called_once_with("동물병원에 가보세요.")
    assert result.answer == "안전한 대체 문구입니다."


@patch("app.services.rag.apply_guardrail", side_effect=lambda x: x)
@patch("app.services.rag.rerank")
@patch("app.services.rag.generate_answer")
@patch("app.services.rag.search_keyword_test")
@patch("app.services.rag.search_similar_test")
@patch("app.services.rag.embed_text")
def test_answer_query_fuses_dense_and_keyword_results_before_rerank(
    mock_embed, mock_search, mock_keyword, mock_generate, mock_rerank, mock_guardrail
):
    """grounded일 때는 실제로 하이브리드 검색(RRF 융합)을 거친 후보가 rerank()에 전달되는지 확인."""
    dense_hit = _sr("벡터로 찾은 문서", 0.9)
    keyword_hit = _sr("키워드로 찾은 문서", 0.05)
    mock_embed.return_value = [0.1, 0.2]
    mock_search.return_value = [dense_hit]
    mock_keyword.return_value = [keyword_hit]
    mock_rerank.return_value = [dense_hit]
    mock_generate.return_value = "답변"

    answer_query("질문")

    mock_keyword.assert_called_once()
    # rerank()에 전달된 후보 목록에 dense/keyword 결과가 모두 포함되어 있어야 함 (RRF 융합 결과).
    call_args = mock_rerank.call_args
    candidates_passed = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs["candidates"]
    assert dense_hit in candidates_passed
    assert keyword_hit in candidates_passed
