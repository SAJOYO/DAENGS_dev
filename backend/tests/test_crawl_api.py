"""관리자 크롤 API — 문(門)과 경계 (RAG-001 요구사항 ②③ · RAG-047).

**여기서 보는 것은 셋이다.**

1. 권한 — 조회는 `READ`, 트리거는 `OPS_WRITE`. 크롤은 외부 사이트로 실제 요청을 내보내고
   `data/raw/` 를 바꾸므로 조회와 같은 문이면 안 된다.
2. 태스크 이름 — 앱은 `daengs_life` 를 import 하지 않고 **이름 문자열**로 보낸다
   (CLAUDE.md 의 "접점은 main.py 세 줄뿐"). 그래서 **이름이 틀려도 앱 쪽에서는 아무 일도
   안 일어난다** — 메시지가 정상으로 나가고 아무도 안 가져가 큐에 쌓일 뿐이다.
   그 어긋남을 잡을 수 있는 자리가 여기뿐이라, 워커의 실제 태스크 이름과 대조한다.
3. 브로커가 없을 때 503 — 앱은 멀쩡하고 워커/브로커가 없는 것이라 500 이 아니다.

DB 는 건드리지 않는다. 조회 경로는 `test_crawl_runs.py` 가 실물로 보고, 여기서는 세션
의존성을 갈아끼운다 — 팀에 DB 가 하나뿐이라 API 테스트가 그것을 읽으러 가면 안 된다.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import create_access_token
from daengs_backend.services import crawl as crawl_service

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from daengs_backend.core.database import get_session
    from daengs_backend.main import app

    app.dependency_overrides[get_session] = lambda: object()
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def _token(role: str = "ADMIN") -> str:
    return create_access_token(uuid.uuid4(), SubjectType.ADMIN, role)


def _auth(role: str = "ADMIN") -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(role)}"}


# ---------------------------------------------------------------- 문
def test_토큰_없이_트리거하면_401(client: TestClient) -> None:
    assert client.post("/admin/crawl", json={"source_ids": []}).status_code == 401


def test_토큰_없이_조회하면_401(client: TestClient) -> None:
    assert client.get("/admin/crawl").status_code == 401


def test_읽기_권한만_있으면_트리거는_403(client: TestClient, monkeypatch) -> None:
    """VIEWER 는 목록은 봐도 크롤을 쏘면 안 된다 — `OPS_WRITE` 가 없다."""
    def _never(*_a, **_k):
        raise AssertionError("권한에서 막혔어야 한다")
    monkeypatch.setattr(crawl_service, "trigger", _never)
    got = client.post("/admin/crawl", json={"source_ids": ["easylaw-pet"]}, headers=_auth("VIEWER"))
    assert got.status_code == 403


# ---------------------------------------------------------------- 태스크 이름 계약
def test_앱은_워커를_import_하지_않고_이름으로_보낸다() -> None:
    """`services/crawl.py` 의 상수가 워커의 `@app.task(name=...)` 과 **문자 그대로** 같아야 한다.

    이 대조가 없으면 이름이 어긋나도 앱은 정상으로 보이고 큐에만 메시지가 쌓인다.
    """
    from daengs_life.tasks.crawl import crawl_due

    assert crawl_service.CRAWL_TASK == crawl_due.name


def test_트리거는_202_와_task_id_를_돌려준다(client: TestClient, monkeypatch) -> None:
    """**결과가 아니라 접수증이다** — 크롤은 분 단위라 요청을 붙들 수 없다 (RAG-001 원칙 6)."""
    sent: dict[str, object] = {}

    def _fake(source_ids=None):
        sent["source_ids"] = list(source_ids or [])
        return "task-abc"

    monkeypatch.setattr(crawl_service, "trigger", _fake)
    got = client.post("/admin/crawl", json={"source_ids": ["easylaw-pet"]}, headers=_auth())
    assert got.status_code == 202
    assert got.json() == {"task_id": "task-abc", "source_ids": ["easylaw-pet"], "note": None}
    assert sent["source_ids"] == ["easylaw-pet"]


def test_소스를_비우면_due_판정은_태스크가_한다(client: TestClient, monkeypatch) -> None:
    """빈 목록을 그대로 넘긴다 — 주기 실행과 **같은 경로**다 (RAG-001 원칙 4)."""
    monkeypatch.setattr(crawl_service, "trigger", lambda source_ids=None: "task-due")
    got = client.post("/admin/crawl", json={}, headers=_auth())
    assert got.status_code == 202 and got.json()["source_ids"] == []


def test_없는_소스도_막지_않는다(client: TestClient, monkeypatch) -> None:
    """시드 목록을 두 곳에서 검사하면 반드시 어긋난다 — 판정은 태스크 한 곳이다 (RAG-044)."""
    monkeypatch.setattr(crawl_service, "trigger", lambda source_ids=None: "task-x")
    got = client.post("/admin/crawl", json={"source_ids": ["없는-소스"]}, headers=_auth())
    assert got.status_code == 202


# ---------------------------------------------------------------- 브로커
def test_브로커가_없으면_503(client: TestClient, monkeypatch) -> None:
    """500 이 아니다 — 앱은 멀쩡하고 워커/브로커가 없는 것이라 사람이 고칠 일이다."""
    def _down(source_ids=None):
        raise crawl_service.BrokerUnavailable("REDIS_URL 이 없다")
    monkeypatch.setattr(crawl_service, "trigger", _down)
    got = client.post("/admin/crawl", json={}, headers=_auth())
    assert got.status_code == 503 and "REDIS_URL" in got.json()["detail"]


def test_redis_url_이_없으면_보내기_전에_막는다(monkeypatch) -> None:
    """브로커 주소가 비어 있으면 Celery 객체를 만들지도 않는다."""
    from daengs_backend.config import settings

    monkeypatch.setattr(settings, "redis_url", "")
    with pytest.raises(crawl_service.BrokerUnavailable):
        crawl_service.trigger(["easylaw-pet"])
