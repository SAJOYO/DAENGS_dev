"""서빙 계층 (RAG-027).

**"HTTP 없이는 의미가 없는 것"만 여기 들어온다.** 도메인은 `realtime/`·`rag/`·`crawler/` 에
있고 FastAPI 없이 단독으로 돈다 (RAG-001 원칙 1) — 그 선을
`tests/test_import_direction.py` 가 기계로 지킨다.

  controllers/  받고, 서비스 부르고, 끝. **로직 0줄** ← 이 구조의 유일한 강제 규칙
  services/     유스케이스 조합 — 도메인 호출 + 응답 조립
  dto/          요청·응답 Pydantic. 도메인 변화가 HTTP 계약으로 새는 것을 막는다
  deps.py       의존성 주입 — 테스트가 여기를 갈아끼운다

`models/` 는 **없다.** 도메인 모델이 이미 도메인 패키지에 있고 ORM 이 없어서다.
ORM 을 들이는 날 생긴다 — 근거와 조건은 RAG-027 에.
"""
