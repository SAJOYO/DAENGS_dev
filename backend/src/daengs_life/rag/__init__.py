"""rag — 제도·문서형 RAG 파트①의 인덱싱 엔진 (RAG-018).

  data/raw  ──parse──▶  processed/parsed  ──chunk──▶  processed/chunks
            ──embed──▶  processed/embeddings  ──load──▶  postgres  ──search──▶  app

`crawler` 는 수집만 한다. 이 패키지는 그 산출물을 검색 가능한 형태로 바꾼다.
의존 방향은 `app → rag → crawler` 한쪽뿐이고 `tests/test_import_direction.py` 가 지킨다.

**배치는 "순서가 있느냐"로 가른다** (RAG-023):

  pipeline.py   단계·순서의 단일 소스. 단계 번호는 바뀌므로 이름이 아니라 여기 리스트에 둔다
  core/         순서가 없는 것 — config · io · ir. 모든 단계가 쓴다
  stages/       순서가 있는 것 — parse/ · chunk · embed · goldenset · (evaluate·load·search·generate)
"""
