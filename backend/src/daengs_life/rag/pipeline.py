"""파이프라인의 단계와 순서 — **순서의 단일 소스** (RAG-023).

순서를 파일 이름에 박지 않은 이유가 여기 있다. `s3_chunk.py` 로 하면 목록만 봐도 순서가 보이지만,
**단계 번호는 계획이지 불변 식별자가 아니다** — RAG-003(하이브리드·리랭커)이 확정되면 8·9단계
사이에 끼어들고 그 순간 뒤 파일이 전부 개명된다. 바뀌는 값은 이름이 아니라 여기 리스트에 둔다.
(RAG-022 ⑥B 가 `chunk_id` 에서 수집 날짜를 뺀 것과 같은 판단이다.)

**`stages/` 에 있으면 여기 한 줄이 있어야 하고, `core/` 에 있으면 없어야 한다.** 그 둘이
"파이프라인의 한 단계인가"의 판정이다.

`rag status`(지금 어디까지 됐나)는 아직 없다. `Stage` 에 판정 함수 필드를 더하고
지금 세 군데에 흩어진 최신 판정 — `io.is_current()` · `io.is_chunk_current()` · `embed` 의
fingerprint — 를 그 뒤로 모으면 된다. 배치 이동과 섞으면 실패했을 때 원인이 둘이 되어 나눴다.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Stage:
    """한 단계. `n` 은 CLAUDE.md 의 9단계 번호이고 **바뀔 수 있다** — 그래서 이름이 아니라 필드다."""
    key: str                      # CLI 서브커맨드 이름 · `stages/{key}.py`
    n: int                        # 실행 순서 (CLAUDE.md 의 단계 번호)
    title: str
    consumes: str                 # 상류 산출물 — 이것이 바뀌면 이 단계를 다시 돌린다
    produces: str
    adr: str


STAGES: list[Stage] = [
    Stage("parse", 2, "원본 → 공통 IR",
          consumes="data/raw/", produces="processed/parsed/", adr="RAG-018·RAG-019"),
    Stage("chunk", 3, "IR → 검색 단위 청크",
          consumes="processed/parsed/", produces="processed/chunks/", adr="RAG-004·RAG-021"),
    Stage("embed", 4, "청크 → 벡터 (모델 3종)",
          consumes="processed/chunks/", produces="processed/embeddings/", adr="RAG-002"),
    Stage("goldenset", 5, "채점 기준표 15문항",
          consumes="processed/chunks/", produces="rag/stages/goldenset.yaml", adr="RAG-022"),
    Stage("evaluate", 6, "3파전 채점 · 승자 1종 선정",
          consumes="processed/embeddings/", produces="processed/eval/", adr="RAG-024"),
    Stage("load", 7, "청크+벡터 → documents 적재",
          consumes="processed/embeddings/", produces="postgres documents", adr="RAG-008·RAG-025"),
    Stage("search", 8, "dense 검색 (검문소③)",
          consumes="postgres documents", produces="stdout", adr="RAG-026"),
    # RAG-003 이 확정되면 search 와 generate 사이에 한 줄이 는다. 파일명은 안 바뀐다 —
    # 그때 고칠 곳이 하나여야 해서 조립 순서를 `generate.ask()` 가 소유한다 (RAG-028 ③).
    Stage("generate", 9, "검색 위에 Gemini 로 답 생성 (검문소④)",
          consumes="postgres documents", produces="processed/answers/", adr="RAG-028"),
]

BY_KEY: dict[str, Stage] = {s.key: s for s in STAGES}


def order(key: str) -> int:
    """서브커맨드 이름 → 실행 순서. CLI 도움말과 `status` 가 같은 순서를 쓰게 한다."""
    return BY_KEY[key].n
