"""Celery 워커·Beat (RAG-009 의 형제 패키지 · RT-002 ②-b).

의존 방향은 `tasks → realtime` · `tasks → crawler` 한쪽이고, 그 **범위**까지
`tests/test_import_direction_packages.py` 가 막는다. crawler 쪽 허용은 RAG-044 에서 셋으로
넓혔다 (`run` · `core.cadence` · `core.registry`) — 넓힌 이유는 그 파일에 적혀 있고,
`store`·`fetch` 는 여전히 밖이다. 워커가 그것을 직접 잡으면 `run` 을 우회하는 두 번째
수집 경로가 생긴다.
"""
