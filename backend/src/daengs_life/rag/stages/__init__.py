"""순서가 있는 것들 — 파이프라인의 단계 (RAG-023).

  parse/      2단계  원본 → 공통 IR       (안에 RAG-018 의 3층이 있다)
  chunk.py    3단계  IR → 검색 단위 청크
  embed.py    4단계  청크 → 벡터 3종
  goldenset   5단계  채점 기준표 15문항
  (evaluate · load · search · generate 가 같은 자리에 붙는다)

**여기 파일을 더하면 `rag/pipeline.py` 의 `STAGES` 에도 한 줄을 더한다.** 순서는 파일 이름이
아니라 그 리스트가 안다 — 단계 번호는 RAG-003 이 끼어들면 바뀌기 때문이다.
"""
