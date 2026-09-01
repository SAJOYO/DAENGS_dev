"""daengs-life crawler — 오케스트레이터 무관 순수 파이썬 패키지 (RAG-001 원칙 1).

app/ 이나 tasks/ 를 import하지 않는다. 의존 방향은 app → crawler, tasks → crawler 한쪽뿐.
실행: python -m crawler run --source easylaw-pet [--limit N] [--dry-run] [--force]
"""
