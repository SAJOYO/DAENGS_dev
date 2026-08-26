# chunking.py = 긴 원문 텍스트를 검색 가능한 "청크(chunk)" 단위로 쪼개는 유틸리티.
#
# 왜 필요한가?
#   긴 문서를 통째로 하나의 벡터로 임베딩하면, 문서 안의 특정 부분(예: "자일리톨 위험성"만)에
#   대한 질문이 들어와도 문서 전체 벡터와 비교하게 되어 검색 정밀도가 떨어집니다.
#   그래서 실제 RAG 시스템은 문서를 작은 청크로 나눠서 각각 따로 임베딩/검색합니다.
#   여기서는 "문장 여러 개를 겹치게(overlap) 묶는" 슬라이딩 윈도우 방식을 씁니다.
#   겹치게 하는 이유: 청크 경계에서 문맥이 잘려 의미가 끊기는 것을 줄이기 위해서입니다.

import re

# 문장 끝을 대략적으로 판별하는 정규식: 마침표/느낌표/물음표 뒤에 공백이 오는 지점.
# (완벽한 문장 분리기는 아님 — "Dr. Smith" 같은 약어도 끊길 수 있음. 완벽한 토크나이저 대신
#  실용적인 수준의 규칙 기반 분리기를 쓰는 건 실제 RAG 파이프라인에서도 흔한 절충입니다.)
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    sentences = _SENTENCE_BOUNDARY.split(text)
    return [s.strip() for s in sentences if s.strip()]


def chunk_text(text: str, max_sentences: int = 3, overlap_sentences: int = 1) -> list[str]:
    """긴 텍스트를 문장 max_sentences개씩, overlap_sentences개씩 겹치게 묶어서 청크 리스트로 반환.

    예: 문장이 [A, B, C, D, E]이고 max_sentences=3, overlap_sentences=1이면
        청크1 = A B C
        청크2 = C D E   (C가 겹침 -> 청크 경계에서 문맥이 뚝 끊기지 않게 함)
    """
    if max_sentences <= overlap_sentences:
        raise ValueError("max_sentences는 overlap_sentences보다 커야 합니다.")

    sentences = split_sentences(text)
    if not sentences:
        return []

    step = max_sentences - overlap_sentences  # 매 반복마다 몇 문장씩 앞으로 나아갈지
    chunks = []
    i = 0
    while i < len(sentences):
        window = sentences[i : i + max_sentences]
        chunks.append(" ".join(window))
        if i + max_sentences >= len(sentences):
            break
        i += step
    return chunks
