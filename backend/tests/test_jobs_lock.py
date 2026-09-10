"""동시 실행 확인 — Cloud Run API 응답을 가짜로 주고 판정만 본다 (D-062 §5)."""
from types import SimpleNamespace

from daengs_life.jobs import lock

ME = "projects/p/locations/asia-northeast3/jobs/corpus-refresh/executions/corpus-refresh-abc"
OTHER = "projects/p/locations/asia-northeast3/jobs/corpus-refresh/executions/corpus-refresh-xyz"


def _ex(name, running):
    return SimpleNamespace(name=name, running_count=1 if running else 0,
                           completion_time=None if running else object())


def test_local_run_without_job_env_is_never_locked():
    assert lock.another_execution_running(job=None, execution=None, project=None, region=None) is None


def test_only_myself_running_is_fine():
    found = lock.another_execution_running(
        job="corpus-refresh", execution="corpus-refresh-abc", project="p", region="asia-northeast3",
        list_executions=lambda parent: [_ex(ME, True)])
    assert found is None


def test_another_running_execution_is_reported():
    found = lock.another_execution_running(
        job="corpus-refresh", execution="corpus-refresh-abc", project="p", region="asia-northeast3",
        list_executions=lambda parent: [_ex(ME, True), _ex(OTHER, True)])
    assert found == "corpus-refresh-xyz"


def test_finished_executions_do_not_count():
    found = lock.another_execution_running(
        job="corpus-refresh", execution="corpus-refresh-abc", project="p", region="asia-northeast3",
        list_executions=lambda parent: [_ex(ME, True), _ex(OTHER, False)])
    assert found is None


def test_parent_path_is_built_from_project_region_job():
    seen = {}
    lock.another_execution_running(
        job="corpus-refresh", execution="e", project="p", region="r",
        list_executions=lambda parent: seen.setdefault("parent", parent) and [])
    assert seen["parent"] == "projects/p/locations/r/jobs/corpus-refresh"


def test_pending_execution_counts_as_active():
    """태스크가 아직 안 뜬 실행은 running_count 가 0 이지만 completion_time 도 없다 — 활성이다 (#325 최종 리뷰)."""
    pending = SimpleNamespace(name=OTHER, running_count=0, completion_time=None)
    found = lock.another_execution_running(
        job="corpus-refresh", execution="corpus-refresh-abc", project="p", region="asia-northeast3",
        list_executions=lambda parent: [_ex(ME, True), pending])
    assert found == "corpus-refresh-xyz"
