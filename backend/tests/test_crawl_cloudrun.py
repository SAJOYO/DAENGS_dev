"""관리자 수동 크롤의 Cloud Run 갈래 (#326, D-062 §3 관리자 트리거).

GCP 를 부르지 않는다 — run_v2 클라이언트를 가짜로 바꿔 갈림·이미 실행 중·API 오류만 본다.
"""
from types import SimpleNamespace

import pytest

from daengs_backend.config import settings


def test_기본_백엔드는_celery_다():
    assert settings.crawl_backend == "celery"
    assert settings.gcp_region == "asia-northeast3"
    assert settings.corpus_job == "corpus-refresh"
    assert settings.gcp_project == ""
