"""`/v1/analyze` 의 업로드 크기 제한(413) 스모크 테스트.

**가중치도 torch 도 없이 돌아야 합니다.** 크기·빈 파일 검사는 `src.pipeline`
(torch·ultralytics 의존)을 import 하기 **전에** 끝나기 때문입니다 — 기본 설치
(`uv sync`, `--extra model` 없음)로 화면과 계약만 보는 개발자도 이 경로를 확인할 수
있어야 한다는 pyproject 의 가름을 지키는 자리입니다.

`test_rejects_oversized_without_model_extra` 가 그 순서를 실제로 지킵니다 —
`serve.py` 에서 import 를 검사보다 위로 되돌리면 그 테스트가 먼저 깨집니다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from src import config  # noqa: E402


@pytest.fixture()
def client(monkeypatch):
    # 실제 150MB 를 주고받지 않도록 한도를 테스트 동안만 1MB 로 줄입니다.
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 1_000_000)
    import serve  # noqa: E402

    return TestClient(serve.app)


def test_oversized_upload_returns_413(client):
    content = b"x" * 2_000_000  # 2MB > 1MB 한도
    resp = client.post(
        "/v1/analyze",
        files={"video": ("dog.mp4", content, "video/mp4")},
    )
    assert resp.status_code == 413
    detail = resp.json()["detail"]
    assert "2.0MB" in detail
    assert "1MB" in detail


def test_empty_upload_returns_400(client):
    resp = client.post(
        "/v1/analyze",
        files={"video": ("dog.mp4", b"", "video/mp4")},
    )
    assert resp.status_code == 400


def test_rejects_oversized_without_model_extra(client, monkeypatch):
    """torch 가 없는 환경(기본 `uv sync`)에서도 413 이어야 합니다.

    `sys.modules` 에 None 을 넣으면 그 모듈의 import 가 ImportError 로 실패합니다 —
    `--extra model` 을 설치하지 않은 상태를 흉내 냅니다. 크기 검사가 import 뒤로
    밀리면 이 테스트가 413 대신 ImportError 를 받아 깨집니다.
    """
    monkeypatch.setitem(sys.modules, "src.pipeline", None)
    monkeypatch.setitem(sys.modules, "src.video_intake", None)

    resp = client.post(
        "/v1/analyze",
        files={"video": ("dog.mp4", b"x" * 2_000_000, "video/mp4")},
    )
    assert resp.status_code == 413
