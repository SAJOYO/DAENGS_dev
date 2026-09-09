"""Celery 없이 **한 프로세스에서 끝까지** 도는 배치 진입점 (D-062).

`tasks/` 는 Celery 워커가 받는 태스크이고 크롤에서 멈춘다. 여기는 Cloud Run Job 이 부르며
crawl → parse → chunk → embed → guard → load 를 순서대로 지난다. 단계 로직은 `crawler` 와
`rag` 에 있고 여기는 **조립과 가드**만 둔다 — 단계 하나를 고칠 일이 생기면 그쪽을 고친다.

의존 방향: `jobs → crawler.{run, core.cadence, core.registry}` · `jobs → rag` · `jobs → tasks.crawl_runs`
(psycopg 만 쓰는 기록 모듈이라 Celery 를 끌고 오지 않는다). 범위는
`tests/test_import_direction_packages.py` 가 막는다.
"""
