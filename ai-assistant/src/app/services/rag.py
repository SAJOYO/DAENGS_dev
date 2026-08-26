# rag.py = 이 앱의 "두뇌". RAG(Retrieval-Augmented Generation, 검색 증강 생성)의 핵심 흐름을 담당.
#
# RAG가 뭔가요?
#   LLM 혼자 답하게 하면 없는 사실을 지어낼 수 있습니다(할루시네이션).
#   그래서 먼저 관련 문서를 "검색(Retrieval)"해서 찾아주고, 그 문서를 근거로 LLM이
#   "답변을 생성(Generation)"하게 만드는 방식이 RAG입니다.
#
# 이 파일의 흐름(answer_query 함수 기준) — 검색 부분은 "넓게 찾고 정밀하게 좁히는"
# retrieve-then-rerank 2단계 구조입니다:
#   질문 텍스트
#     -> (1) embed_text            : 질문을 벡터로 변환
#     -> (2a) search_similar       : 벡터 유사도로 넓게(retrieval_pool_size개) 후보 검색 (dense)
#     -> (2b) search_keyword       : 키워드 매칭으로도 넓게 후보 검색 (sparse)
#     -> (2c) reciprocal_rank_fusion : 두 순위를 하나로 융합
#     -> (2d) rerank                : LLM이 융합된 후보를 정밀 재채점 -> 최종 top_k 선정
#     -> (3) generate_answer       : 찾은 문서(or 없으면 빈 값)를 근거로 LLM이 답변 문장 생성
#     -> (4) apply_guardrail       : 위험한 표현 있으면 걸러내기
#     -> ChatResult 반환

from dataclasses import dataclass

from app.config import get_settings
from app.repository_documents_test import search_keyword_test, search_similar_test
from app.services.embedding import embed_text
from app.services.generation import generate_answer
from app.services.guardrail import apply_guardrail
from app.services.hybrid_search import reciprocal_rank_fusion
from app.services.reranker import rerank


# @dataclass : "필드 이름 : 타입"만 적으면 __init__(생성자) 등을 자동으로 만들어주는 데코레이터.
# 즉 아래는 직접 __init__을 쓰지 않아도 Source(content="...", score=0.9) 처럼 바로 만들 수 있게 해줌.
# (pydantic BaseModel과 비슷하지만, dataclass는 검증 기능이 없는 "가벼운 데이터 묶음"이라고 생각하면 됨.
#  API 요청/응답처럼 외부 입력을 검증해야 할 땐 pydantic, 내부에서만 쓰는 값 묶음엔 dataclass를 씀.)
@dataclass
class Source:
    content: str
    score: float
    source_url: str | None = None
    section: str | None = None


@dataclass
class ChatResult:
    answer: str
    sources: list[Source]
    grounded: bool  # 실제 문서에 근거해서 답했는지 여부


def answer_query(query: str) -> ChatResult:
    settings = get_settings()

    # (1) 질문을 벡터로 변환
    query_embedding = embed_text(query)

    # (2a) dense: 벡터 유사도로 넓게 후보 검색. 정렬 기준이 코사인 유사도이므로
    #      dense_results[0]은 top_k를 몇으로 넓히든 항상 "가장 유사한 문서"로 동일함.
    dense_results = search_similar_test(query_embedding, top_k=settings.retrieval_pool_size)

    # BR2: top-1 "벡터" 유사도가 임계값 이상이어야 근거 있는 답변(US-01)으로 취급.
    # 이 판정은 하이브리드 융합/리랭킹 이전의 순수 벡터 점수로 하는데, 이미 검증된 임계값(0.5)
    # 의미를 그대로 유지하기 위함 — 리랭킹은 "어떤 문서를 보여줄지"만 조정하고,
    # "애초에 관련 문서가 있긴 한지"는 원래 방식 그대로 판단합니다.
    grounded = bool(dense_results) and dense_results[0].score >= settings.similarity_threshold

    if grounded:
        # (2b) sparse: 근거가 있을 때만 키워드(전문 검색) 후보도 넓게 검색
        #      (벡터가 놓칠 수 있는 정확한 용어 보완). ungrounded 경로에서는 어차피 안 쓰이므로 생략.
        keyword_results = search_keyword_test(query, top_k=settings.retrieval_pool_size)
        # (2c) RRF로 dense/sparse 두 순위를 하나로 융합 -> 후보 풀 구성.
        fused_candidates = reciprocal_rank_fusion(dense_results, keyword_results)[
            : settings.rerank_pool_size
        ]
        # (2d) LLM 리랭커로 후보를 정밀 재채점해서 최종 top_k만 선정.
        results = rerank(query, fused_candidates, top_k=settings.top_k)

        # 근거 문서가 있으면: 그 내용들을 LLM에게 참고자료로 같이 넘겨서 답변 생성.
        # source_url이 있으면 스니펫에 출처를 함께 적어, LLM이 답변에 실제 출처를 인용할 수 있게 함.
        context_snippets = [
            f"{r.content} (근거: {r.section})" if r.section else
            (f"{r.content} (출처: {r.source_url})" if r.source_url else r.content)
            for r in results
        ]
        draft_answer = generate_answer(query, context_snippets)
        sources = [
            Source(content=r.content, score=r.score, source_url=r.source_url, section=r.section)
            for r in results
        ]
    else:
        # US-02: 근거 없이도 일반 지식으로 우선 시도, 확신 없으면 모른다고 답하도록 프롬프트에서 유도
        draft_answer = generate_answer(query, [])  # 빈 리스트 -> "참고 문서 없음" 프롬프트로 전환됨
        sources = []

    # (4) LLM이 만든 초안(draft_answer)을 가드레일에 통과시켜 최종 답변 확정.
    final_answer = apply_guardrail(draft_answer)

    return ChatResult(answer=final_answer, sources=sources, grounded=grounded)
