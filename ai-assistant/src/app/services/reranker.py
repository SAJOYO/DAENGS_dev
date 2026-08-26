# reranker.py = 1차로 찾아온 후보 문서들을, LLM에게 "질문과 얼마나 관련 있는지" 직접
# 채점시켜서 다시 정렬하는 단계 (검색 파이프라인의 마지막 정밀 필터).
#
# 왜 필요한가?
#   벡터/키워드 검색(hybrid_search.py)은 빠르지만 다소 거친 1차 후보 선별입니다.
#   리랭커는 후보 수를 좁힌 뒤(예: 8개), 더 무겁지만 정확한 LLM 판단으로 진짜 관련 있는
#   것만 상위로 올립니다. "넓게 검색 -> 좁게 정밀 재정렬"은 실제 RAG 시스템에서 흔히
#   쓰는 2단계 검색(retrieve-then-rerank) 패턴입니다.
#
# 왜 번역(ingest_real_sources.py)과 다르게 로컬 LLM을 믿고 쓰는가?
#   번역은 "사실을 있는 그대로 옮겨야 하는" 작업이라 모델이 틀리면 잘못된 정보가 영구히
#   저장됩니다 (실제로 qwen2.5:7b가 오역한 사례가 있었음). 반면 리랭킹은 "이미 검색된
#   후보들의 순서만 바꾸는" 작업이라, 모델이 다소 부정확해도 최악의 경우 순서가 조금
#   덜 좋아지는 정도이지 잘못된 정보가 생기진 않습니다. 그래서 상대적으로 안전하게
#   로컬 LLM을 활용할 수 있는 지점입니다.

import re
from dataclasses import replace

import ollama

from app.config import get_settings
from app.repository_documents_test import SearchTestResult

# 후보마다 LLM을 따로 호출하면(원래 방식) 질의 1건당 최대 rerank_pool_size번의 순차 왕복이
# 생겨 응답이 느려집니다 (VRAM이 빠듯한 GPU에서는 모델 스왑까지 겹쳐 더 심함). 그래서 후보를
# 전부 번호 매겨 프롬프트 하나에 넣고, LLM이 한 번의 응답으로 모든 번호를 채점하게 합니다.
RERANK_SYSTEM_PROMPT = (
    "당신은 검색 결과의 관련성을 평가하는 채점자입니다. "
    "주어진 질문과 번호가 매겨진 문서 목록을 보고, 각 문서가 질문에 답하는 데 얼마나 직접적으로 "
    "도움이 되는지 0에서 10 사이의 정수로 채점하세요 (10 = 매우 관련 있음, 0 = 전혀 관련 없음). "
    "문서 개수만큼, 한 줄에 하나씩 '번호: 점수' 형식으로만 출력하고 다른 설명은 절대 덧붙이지 마세요."
)

_SCORE_LINE_PATTERN = re.compile(r"(\d+)\s*[:.\)]\s*(\d+(?:\.\d+)?)")


def _score_all(query: str, candidates: list[SearchTestResult]) -> list[float]:
    settings = get_settings()
    client = ollama.Client(host=settings.ollama_host)
    listing = "\n".join(f"{i}: {c.content}" for i, c in enumerate(candidates))
    response = client.chat(
        model=settings.generation_model,
        messages=[
            {"role": "system", "content": RERANK_SYSTEM_PROMPT},
            {"role": "user", "content": f"질문: {query}\n\n문서 목록:\n{listing}"},
        ],
        # temperature=0 : 채점 결과가 매번 들쭉날쭉하지 않도록 (같은 입력엔 같은 출력에 가깝게).
        options={"temperature": 0},
    )
    text = response["message"]["content"]

    scores: dict[int, float] = {}
    for match in _SCORE_LINE_PATTERN.finditer(text):
        idx = int(match.group(1))
        if 0 <= idx < len(candidates) and idx not in scores:
            scores[idx] = max(0.0, min(10.0, float(match.group(2))))

    # 모델이 형식을 안 지켜서 특정 번호를 못 뽑아낸 경우, 그 후보는 최하위로 밀어내는 게
    # "원래 순서를 믿는 것"보다 안전한 폴백입니다 — 검증 안 된 값으로 상위 노출시키지 않음.
    return [scores.get(i, -1.0) for i in range(len(candidates))]


def rerank(query: str, candidates: list[SearchTestResult], top_k: int) -> list[SearchTestResult]:
    if not candidates:
        return []
    relevance_scores = _score_all(query, candidates)
    scored = list(zip(candidates, relevance_scores))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    top = scored[:top_k]

    # candidate.score를 LLM 관련성 점수(0~10 -> 0~1로 정규화)로 덮어씀.
    # 이유: candidate는 dense(코사인 유사도, 0~1)와 sparse(ts_rank_cd, 스케일이 전혀 다름) 두 곳
    # 중 어디서 왔는지에 따라 score의 "의미"가 달랐음 (RRF는 순위만 합칠 뿐 score 필드는
    # 건드리지 않으므로). 리랭킹을 거친 뒤에는 "이 문서가 실제로 질문과 얼마나 관련 있는가"라는
    # 하나의 일관된 의미로 score를 다시 채워서, API 응답의 sources[].score가 항상 같은 척도를
    # 갖도록 함. dataclasses.replace()는 기존 객체를 복사하면서 지정한 필드만 바꿔 새 객체를 만듦.
    return [replace(candidate, score=relevance / 10.0) for candidate, relevance in top]
