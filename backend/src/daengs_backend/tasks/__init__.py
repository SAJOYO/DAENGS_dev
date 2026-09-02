"""backend 소유의 Celery 태스크 (D-043).

⚠️ **`daengs_life.tasks.celery_app` 을 쓰지 않습니다 — 일부러입니다.** 그쪽에 gait
   태스크를 등록하면 D-021 이 세 줄로 못박은 backend→life 접점이 넓어집니다.
   같은 Redis 브로커에 **앱 인스턴스만 따로** 둡니다. Celery 는 브로커를 공유하는
   여러 앱을 문제없이 허용하고, 큐 이름(`gait`)이 갈라 주므로 crawler(`crawl`)와
   서로를 보지 못합니다.
"""
