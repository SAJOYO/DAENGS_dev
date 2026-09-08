"""Cloud Run Jobs 를 부르는 얇은 래퍼 (#326, D-062 §3 관리자 트리거).

`daengs_life.jobs.lock` 과 비슷한 일을 하지만 **그것을 import 하지 않는다** — `daengs_backend` 가
`daengs_life` 를 부르는 접점은 `main.py` 세 줄뿐이다(CLAUDE.md). 여기 20줄이 그 선을 지키는 값이다.

클라이언트는 인자로 받는다(`executions_client`·`jobs_client`). 테스트가 가짜를 넣고, 운영은 None 으로
불러 ADC(VM 메타데이터 서버)로 만든다. 키 파일은 없다. **기본 클라이언트는 모듈에 한 번만 만들어
캐시한다** — 상태 페이지가 2초 예산 안에서 자주 부르는 경로라, 매번 새 채널을 여는 비용이 그대로
누적된다(#326 리뷰 라운드 1). 주입된 클라이언트(테스트)는 이 캐시를 거치지 않는다.

google API 예외는 여기서 안 잡는다 — 부르는 쪽(`services/crawl.py`)이 `BrokerUnavailable` 로 바꾼다.
"""

from __future__ import annotations

from collections.abc import Sequence

#: 기본 클라이언트 캐시. 키는 "executions"·"jobs". 주입된 클라이언트는 여기 안 거칩니다.
_clients: dict[str, object] = {}


def job_path(project: str, region: str, job: str) -> str:
    return f"projects/{project}/locations/{region}/jobs/{job}"


def _executions_client():
    if "executions" not in _clients:
        from google.cloud import run_v2

        _clients["executions"] = run_v2.ExecutionsClient()
    return _clients["executions"]


def _jobs_client():
    if "jobs" not in _clients:
        from google.cloud import run_v2

        _clients["jobs"] = run_v2.JobsClient()
    return _clients["jobs"]


def active_execution(
    project: str, region: str, job: str, *, executions_client=None, timeout: float | None = None
) -> str | None:
    """끝나지 않은 실행(pending 포함)의 짧은 이름. 없으면 None.

    `completion_time` 이 없으면 활성으로 본다 — `running_count` 를 보면 아직 태스크가 안 뜬 pending
    실행을 놓친다(#325 최종 리뷰). `timeout` 은 그대로 `list_executions` 에 넘어간다 — 상태 페이지의
    짧은 예산 안에서 API 가 안 답할 때 스레드가 무한정 물려 있지 않게 하려는 것이다(#326 라운드 1).
    """
    client = executions_client or _executions_client()
    executions = client.list_executions(parent=job_path(project, region, job), timeout=timeout)
    for ex in executions:
        if getattr(ex, "completion_time", None) is None:
            return ex.name.rsplit("/", 1)[-1]
    return None


def run(
    project: str, region: str, job: str, args: Sequence[str], *, jobs_client=None,
    timeout: float | None = None,
) -> str:
    """잡을 한 번 실행하고 실행의 짧은 이름을 돌려준다. `args` 는 컨테이너 인자 override 다."""
    from google.cloud import run_v2

    client = jobs_client or _jobs_client()
    overrides = run_v2.RunJobRequest.Overrides()
    if args:
        overrides.container_overrides.append(
            run_v2.RunJobRequest.Overrides.ContainerOverride(args=list(args)))
    request = run_v2.RunJobRequest(name=job_path(project, region, job), overrides=overrides)
    operation = client.run_job(request=request, timeout=timeout)
    return operation.metadata.name.rsplit("/", 1)[-1]


def job_exists(
    project: str, region: str, job: str, *, jobs_client=None, timeout: float | None = None
) -> bool:
    from google.api_core.exceptions import NotFound

    client = jobs_client or _jobs_client()
    try:
        client.get_job(name=job_path(project, region, job), timeout=timeout)
    except NotFound:
        return False
    return True


__all__ = ["active_execution", "job_exists", "job_path", "run"]
