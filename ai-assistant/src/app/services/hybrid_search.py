# hybrid_search.py = 벡터 검색(dense) 결과와 키워드 검색(sparse) 결과를
# "하나의 순위"로 합치는 로직. RRF(Reciprocal Rank Fusion)라는 잘 알려진 표준 기법을 씁니다.
#
# 왜 RRF인가?
#   벡터 유사도 점수(0~1)와 전문 검색 점수(ts_rank_cd, 스케일이 전혀 다름)는 숫자를
#   그대로 더하거나 비교할 수 없습니다 (단위가 다른 두 점수를 섞는 셈이라 무의미함).
#   RRF는 점수 자체가 아니라 "순위(rank)"만 보고 합치기 때문에, 이런 스케일 문제가 없습니다.
#   공식: 문서 하나의 RRF 점수 = 각 검색 결과 리스트에서 그 문서가 있었던 순위들에 대해
#         1 / (k + 순위) 를 전부 더한 값. k는 상위 결과에 너무 쏠리지 않게 하는 완충 상수
#         (관례적으로 60을 많이 씀).

from app.repository_documents_test import SearchTestResult


def reciprocal_rank_fusion(
    *ranked_lists: list[SearchTestResult], k: int = 60
) -> list[SearchTestResult]:
    """여러 개의 순위 리스트(예: 벡터 검색 결과, 키워드 검색 결과)를 하나로 합쳐서
    RRF 점수가 높은 순으로 정렬한 리스트를 반환.

    같은 문서가 여러 리스트에 동시에 등장하면(즉 벡터로도, 키워드로도 모두 찾힌 문서)
    두 순위의 기여분이 더해지므로 자연스럽게 더 높은 순위로 올라갑니다.
    """
    # content 문자열을 문서의 식별자로 사용 (SearchResult에는 DB id가 없고, content가
    # 사실상 유일한 값이라 이걸로 "같은 문서"인지 판별함).
    rrf_scores: dict[str, float] = {}
    result_by_content: dict[str, SearchTestResult] = {}

    for ranked_list in ranked_lists:
        for rank, result in enumerate(ranked_list, start=1):  # rank는 1부터 시작
            rrf_scores[result.content] = rrf_scores.get(result.content, 0.0) + 1.0 / (k + rank)
            result_by_content[result.content] = result  # 마지막에 본 버전을 대표로 사용

    # dict.values()를 RRF 점수 기준 내림차순으로 정렬.
    fused = sorted(result_by_content.values(), key=lambda r: rrf_scores[r.content], reverse=True)
    return fused
