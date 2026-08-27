"""순서가 없는 것들 — 모든 단계가 쓴다 (RAG-023).

여기에 있으면 파이프라인의 한 단계가 **아니다.** 단계는 `rag/stages/` 에 있고
`rag/pipeline.py` 가 그 순서를 안다.

  config  경로. data/ 탐색은 crawler.core.config 것을 그대로 쓴다 (RAG-014)
  io      raw/·parsed/·chunks/ 입출력, 상류 해시 비교로 재실행 스킵
  ir      공통 중간 표현 6종 + Chunk/ChunkSet — 계층 간 계약 (RAG-018)
"""
