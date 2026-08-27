"""Celery 워커·Beat (RAG-009 의 형제 패키지 · RT-002 ②-b).

의존 방향은 `tasks → realtime → crawler.core.config` 한쪽이고, 그 **범위**까지
`tests/test_import_direction_packages.py` 가 막는다.
"""
