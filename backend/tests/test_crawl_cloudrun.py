"""관리자 수동 크롤의 Cloud Run 갈래 (#326, D-062 §3 관리자 트리거).

GCP 를 부르지 않는다 — run_v2 클라이언트를 가짜로 바꿔 갈림·이미 실행 중·API 오류만 본다.
"""
from types import SimpleNamespace

from daengs_backend.config import settings


def test_기본_백엔드는_celery_다():
    assert settings.crawl_backend == "celery"
    assert settings.gcp_region == "asia-northeast3"
    assert settings.corpus_job == "corpus-refresh"
    assert settings.gcp_project == ""


from daengs_backend.services import cloudrun_jobs as cr

JOB = "projects/p/locations/r/jobs/corpus-refresh"


def _ex(name, done):
    return SimpleNamespace(name=f"{JOB}/executions/{name}",
                           completion_time=object() if done else None)


class FakeExecutions:
    def __init__(self, items): self.items = items; self.parent = None
    def list_executions(self, parent): self.parent = parent; return self.items


class FakeJobs:
    def __init__(self): self.requests = []
    def run_job(self, request):
        self.requests.append(request)
        return SimpleNamespace(metadata=SimpleNamespace(name=f"{JOB}/executions/corpus-refresh-new1"))
    def get_job(self, name): return SimpleNamespace(name=name)


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
    fj = FakeJobs()
    cr.run("p", "r", "corpus-refresh", [], jobs_client=fj)
    req = fj.requests[0]
    assert not req.overrides.container_overrides


def test_job_exists():
    assert cr.job_exists("p", "r", "corpus-refresh", jobs_client=FakeJobs()) is True

    from google.api_core.exceptions import NotFound

    class Missing:
        def get_job(self, name): raise NotFound("no")
    assert cr.job_exists("p", "r", "corpus-refresh", jobs_client=Missing()) is False
