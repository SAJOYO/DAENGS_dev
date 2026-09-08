"""관리자 수동 크롤의 Cloud Run 갈래 (#326, D-062 §3 관리자 트리거).

GCP 를 부르지 않는다 — run_v2 클라이언트를 가짜로 바꿔 갈림·이미 실행 중·API 오류만 본다.
"""
from types import SimpleNamespace

import pytest

from daengs_backend.config import settings
from daengs_backend.schemas.status import StatusState
from daengs_backend.services import cloudrun_jobs as cr
from daengs_backend.services import crawl as crawl_service
from daengs_backend.services import status as status_service
from tests.test_crawl_api import _auth, client  # noqa: F401 — client 는 fixture 로만 쓰인다(재사용)

JOB = "projects/p/locations/r/jobs/corpus-refresh"


def test_기본_백엔드는_celery_다():
    assert settings.crawl_backend == "celery"
    assert settings.gcp_region == "asia-northeast3"
    assert settings.corpus_job == "corpus-refresh"
    assert settings.gcp_project == ""


def _ex(name, done):
    return SimpleNamespace(name=f"{JOB}/executions/{name}",
                           completion_time=object() if done else None)


class _FakePage:
    def __init__(self, executions): self.executions = executions


class _FakePager:
    """`ListExecutionsPager` 흉내 — `.pages` 가 페이지(첫 페이지만)를 낸다(#326 최종 리뷰 미너 8)."""
    def __init__(self, items): self._items = items
    @property
    def pages(self):
        yield _FakePage(self._items)


class FakeExecutions:
    def __init__(self, items): self.items = items; self.parent = None
    def list_executions(self, request=None, parent=None, **kwargs):
        # 운영 코드는 이제 `request=` 로 부른다 — `parent=` 는 옛 호출 방식과의 호환용이다.
        self.parent = request.parent if request is not None else parent
        return _FakePager(self.items)


class FakeJobs:
    def __init__(self): self.requests = []
    def run_job(self, request, **kwargs):
        self.requests.append(request)
        return SimpleNamespace(metadata=SimpleNamespace(name=f"{JOB}/executions/corpus-refresh-new1"))
    def get_job(self, name, **kwargs): return SimpleNamespace(name=name)


def test_job_path():
    assert cr.job_path("p", "r", "corpus-refresh") == JOB


def test_끝나지_않은_실행이_있으면_그_이름():
    fx = FakeExecutions([_ex("old", True), _ex("pending", False)])
    assert cr.active_execution("p", "r", "corpus-refresh", executions_client=fx) == "pending"
    assert fx.parent == JOB


def test_전부_끝났으면_None():
    fx = FakeExecutions([_ex("a", True), _ex("b", True)])
    assert cr.active_execution("p", "r", "corpus-refresh", executions_client=fx) is None


def test_run_은_인자를_override_로_넘기고_실행_이름을_돌려준다():
    fj = FakeJobs()
    name = cr.run("p", "r", "corpus-refresh", ["--sources", "a", "b"], jobs_client=fj)
    assert name == "corpus-refresh-new1"
    req = fj.requests[0]
    assert req.name == JOB
    assert list(req.overrides.container_overrides[0].args) == ["--sources", "a", "b"]


def test_run_은_인자가_없으면_override_를_안_붙인다():
    """빈 `Overrides()` 도 안 된다 — `task_count=0` 까지 같이 직렬화된다(#326 최종 리뷰 미너 3).
    proto-plus 에서는 `not req.overrides` 로 필드 부재를 못 본다 — `in` 으로 존재 자체를 본다."""
    fj = FakeJobs()
    cr.run("p", "r", "corpus-refresh", [], jobs_client=fj)
    req = fj.requests[0]
    assert "overrides" not in req


def test_job_exists():
    assert cr.job_exists("p", "r", "corpus-refresh", jobs_client=FakeJobs()) is True

    from google.api_core.exceptions import NotFound

    class Missing:
        def get_job(self, name, **kwargs): raise NotFound("no")
    assert cr.job_exists("p", "r", "corpus-refresh", jobs_client=Missing()) is False


# ---------------------------------------------------------------- services/crawl.py 의 cloudrun 갈래
def _cloudrun(monkeypatch, *, active=None, project="p"):
    monkeypatch.setattr(settings, "crawl_backend", "cloudrun")
    monkeypatch.setattr(settings, "gcp_project", project)
    calls = {}
    monkeypatch.setattr(cr, "active_execution", lambda *a, **k: active)

    def _run(p, r, j, args, **k):
        calls["run"] = (p, r, j, list(args))
        return "corpus-refresh-x1"

    monkeypatch.setattr(cr, "run", _run)
    monkeypatch.setattr(cr, "job_exists", lambda *a, **k: True)
    return calls


def test_cloudrun_트리거는_잡을_실행하고_실행_이름을_돌려준다(monkeypatch):
    calls = _cloudrun(monkeypatch)
    assert crawl_service.trigger(["a", "b"]) == "corpus-refresh-x1"
    assert calls["run"] == ("p", "asia-northeast3", "corpus-refresh", ["--sources", "a", "b"])


def test_cloudrun_소스가_없으면_인자_없이(monkeypatch):
    calls = _cloudrun(monkeypatch)
    crawl_service.trigger(None)
    assert calls["run"][3] == []


def test_cloudrun_이미_실행_중이면_새로_안_띄운다(monkeypatch):
    calls = _cloudrun(monkeypatch, active="corpus-refresh-old")
    with pytest.raises(crawl_service.AlreadyRunning) as e:
        crawl_service.trigger([])
    assert e.value.execution == "corpus-refresh-old"
    assert "run" not in calls


def test_cloudrun_프로젝트가_없으면_BrokerUnavailable(monkeypatch):
    _cloudrun(monkeypatch, project="")
    with pytest.raises(crawl_service.BrokerUnavailable):
        crawl_service.trigger([])


def test_cloudrun_API_오류는_BrokerUnavailable(monkeypatch):
    _cloudrun(monkeypatch)
    def boom(*a, **k): raise RuntimeError("403")
    monkeypatch.setattr(cr, "active_execution", boom)
    with pytest.raises(crawl_service.BrokerUnavailable):
        crawl_service.trigger([])


def test_cloudrun_crawl_workers_는_잡_존재로_답한다(monkeypatch):
    _cloudrun(monkeypatch)
    assert crawl_service.crawl_workers() == ["corpus-refresh@asia-northeast3"]
    monkeypatch.setattr(cr, "job_exists", lambda *a, **k: False)
    assert crawl_service.crawl_workers() == []


def test_cloudrun_crawl_workers_는_timeout_을_job_exists_에_전달한다(monkeypatch):
    """상태 페이지가 `WORKER_PING_SEC` 예산을 넘기지 않으려면 여기까지 전달돼야 한다(#326 라운드 1)."""
    _cloudrun(monkeypatch)
    seen = {}

    def _job_exists(*a, **k):
        seen.update(k)
        return True

    monkeypatch.setattr(cr, "job_exists", _job_exists)
    crawl_service.crawl_workers(1.5)
    assert seen.get("timeout") == 1.5


def test_celery_갈래는_그대로다(monkeypatch):
    monkeypatch.setattr(settings, "crawl_backend", "celery")
    monkeypatch.setattr(settings, "redis_url", "")
    with pytest.raises(crawl_service.BrokerUnavailable):
        crawl_service.trigger(["a"])


# ---------------------------------------------------------------- 라우터: 202 의 note
def test_이미_실행_중이면_202_와_note(client, monkeypatch):  # noqa: F811 — client 는 fixture 인자다
    def _busy(source_ids=None):
        raise crawl_service.AlreadyRunning("corpus-refresh-old")
    monkeypatch.setattr(crawl_service, "trigger", _busy)
    got = client.post("/admin/crawl", json={}, headers=_auth())
    assert got.status_code == 202
    body = got.json()
    assert body["task_id"] == "corpus-refresh-old"
    assert "실행 중" in body["note"]


def test_보통_202_에는_note_가_없다(client, monkeypatch):  # noqa: F811 — client 는 fixture 인자다
    monkeypatch.setattr(crawl_service, "trigger", lambda source_ids=None: "t1")
    got = client.post("/admin/crawl", json={}, headers=_auth())
    assert got.status_code == 202 and got.json()["note"] is None


# ---------------------------------------------------------------- 상태 페이지: absent vs down
async def _no_runs(_session):
    return []


async def _zero_running(_session):
    return 0


async def test_상태_페이지는_cloudrun_워커_이름을_잡_이름으로_보여준다(monkeypatch):
    """워커 대수가 아니라 잡 이름이다 — Cloud Run 에는 "대수" 개념이 없다(#326 라운드 1)."""
    monkeypatch.setattr(settings, "crawl_backend", "cloudrun")
    monkeypatch.setattr(settings, "gcp_project", "p")
    monkeypatch.setattr(crawl_service, "latest", _no_runs)
    monkeypatch.setattr(crawl_service, "running_count", _zero_running)
    monkeypatch.setattr(crawl_service, "crawl_workers",
                        lambda *a, **k: ["corpus-refresh@asia-northeast3"])

    state, detail = await status_service._crawl(object())
    assert state == StatusState.OK
    assert "Cloud Run 잡 corpus-refresh@asia-northeast3" in detail


async def test_상태_페이지는_cloudrun_API_고장을_없음이_아니라_down_으로_본다(monkeypatch):
    """`BrokerUnavailable` 이 곧 "여기엔 크롤러가 없다"가 아니다 — 설정은 있는데 API 가 안
    답하는 것일 수 있고, 그때는 고장이다(#326 리뷰 라운드 1)."""
    monkeypatch.setattr(settings, "crawl_backend", "cloudrun")
    monkeypatch.setattr(settings, "gcp_project", "p")
    monkeypatch.setattr(crawl_service, "latest", _no_runs)

    def _broken(*a, **k):
        raise crawl_service.BrokerUnavailable("Cloud Run 에 묻지 못했다")

    monkeypatch.setattr(crawl_service, "crawl_workers", _broken)

    state, detail = await status_service._crawl(object())
    assert state == StatusState.DOWN
    assert "Cloud Run 잡에 묻지 못했습니다" in detail


async def test_상태_페이지는_cloudrun_잡이_없으면_없음이_아니라_down_으로_본다(monkeypatch):
    """`crawl_workers` 가 예외 없이 빈 목록을 돌려주는 경우 — API 는 멀쩡히 답했지만 잡 자체가
    없다(`job_exists` 가 `False`). 배포가 안 됐거나 이름이 틀린 것이라 "환경에 원래 없다"가
    아니라 "있어야 하는데 없다"라 `down` 이다(#326 최종 리뷰 미너 4)."""
    monkeypatch.setattr(settings, "crawl_backend", "cloudrun")
    monkeypatch.setattr(settings, "gcp_project", "p")
    monkeypatch.setattr(settings, "corpus_job", "corpus-refresh")
    monkeypatch.setattr(settings, "gcp_region", "asia-northeast3")
    monkeypatch.setattr(crawl_service, "latest", _no_runs)
    monkeypatch.setattr(crawl_service, "crawl_workers", lambda *a, **k: [])

    state, detail = await status_service._crawl(object())
    assert state == StatusState.DOWN
    assert "corpus-refresh" in detail
    assert "asia-northeast3" in detail
