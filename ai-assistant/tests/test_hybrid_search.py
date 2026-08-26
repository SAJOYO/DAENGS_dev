from uuid import uuid4

from app.repository_documents_test import SearchTestResult
from app.services.hybrid_search import reciprocal_rank_fusion


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


def test_rrf_ranks_document_found_in_both_lists_highest():
    both = _sr("both", 0.5)
    dense_only = _sr("dense_only", 0.9)
    keyword_only = _sr("keyword_only", 0.9)

    dense_list = [dense_only, both]
    keyword_list = [both, keyword_only]

    fused = reciprocal_rank_fusion(dense_list, keyword_list)

    # 두 리스트 모두에 등장한 문서가 RRF 점수를 두 번 받으므로 1위여야 함.
    assert fused[0].content == "both"
    assert {r.content for r in fused} == {"both", "dense_only", "keyword_only"}


def test_rrf_handles_empty_list():
    dense_list = [_sr("only", 0.9)]
    fused = reciprocal_rank_fusion(dense_list, [])
    assert [r.content for r in fused] == ["only"]


def test_rrf_handles_both_empty():
    assert reciprocal_rank_fusion([], []) == []


def test_rrf_higher_rank_position_scores_higher_when_no_overlap():
    a = _sr("a", 0.9)
    b = _sr("b", 0.8)
    fused = reciprocal_rank_fusion([a, b], [])
    assert [r.content for r in fused] == ["a", "b"]
