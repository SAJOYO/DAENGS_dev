"""realtime — 파트② 실시간 조회형 엔진 (RT-001).

  GPS ──geo──▶ 조회 키(격자·측정소·행정동) ──providers──▶ 공통 관측 모델 ──rules──▶ 산책 적합도

`crawler`·`rag` 와 달리 **저장하지 않는다** — 10분 뒤면 틀릴 값을 벡터로 박아두면 계속 오답을
뱉는다 (`data/README.md` §5, RAG-012). 그래서 소스 모듈도 `.meta.json` 도 생기지 않고,
`data/` 에 두는 것은 측정소 목록 같은 **정적 메타뿐**이다.

층은 둘이다 (RT-001 ①) — 전송 계약이 기관을 가로지르기 때문이다. 기상청 하나가 data.go.kr ·
apihub typ01 · apihub typ02 셋에 걸쳐 있어서, 기관 축으로 자르면 한 모듈 안에 인증 2종 ·
응답 포맷 3종이 들어간다.

  transport/   전송 층 — 인증·봉투·에러코드·재시도 (3 모듈)
  providers/   소스 층 — API 서비스 하나 = 모듈 하나 (7 모듈)

`core/` 는 두지 않는다 (RT-001 ①-3) — 그 자리는 `transport/` 가 이미 차지했다.

의존 방향은 `app → realtime → crawler.core.config` 한쪽뿐이고, 그 **하나뿐**이라는 것까지
`tests/test_import_direction_packages.py` 가 지킨다.
"""
