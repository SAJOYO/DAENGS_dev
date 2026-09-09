"""같은 잡이 이미 돌고 있으면 건너뛴다 (D-062 §5).

Cloud Run Job 에는 "동시 실행 1개" 설정이 없다. Scheduler 발사와 관리자 트리거(#326)가 겹치면
두 잡이 같은 버킷에 쓰고 해시 파일이 꼬인다. 버킷 잠금 파일 대신 API 를 묻는 이유 — 잡이 죽어
잠금이 남는 문제가 없다(실행이 끝나면 API 가 그렇게 답한다).

Cloud Run 이 잡 컨테이너에 넣어 주는 env: `CLOUD_RUN_JOB`(잡 이름) · `CLOUD_RUN_EXECUTION`(이번
실행 이름). 프로젝트·리전은 자동으로 안 오므로 잡 정의에서 `DAENGS_GCP_PROJECT`·`DAENGS_GCP_REGION`
으로 넣는다 (`infra/gcp/pipeline.sh`). 넷 중 하나라도 없으면 로컬 실행으로 보고 확인을 건너뛴다.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable

log = logging.getLogger(__name__)


def _default_list_executions(parent: str) -> Iterable:
    """첫 페이지(20개)만 본다 — 실행은 최신순으로 오므로 안 끝난 것을 찾는 데 넉넉하다
    (`daengs_backend/services/cloudrun_jobs.py` 의 `active_execution` 과 같은 판단, #326 최종 리뷰)."""
    from google.cloud import run_v2

    client = run_v2.ExecutionsClient()
    request = run_v2.ListExecutionsRequest(parent=parent, page_size=20)
    pager = client.list_executions(request=request)
    return next(iter(pager.pages)).executions


def another_execution_running(*, job: str | None, execution: str | None,
                              project: str | None, region: str | None,
                              list_executions: Callable[[str], Iterable] | None = None) -> str | None:
    """다른 실행이 돌고 있으면 그 짧은 이름, 아니면 None."""
    if not (job and execution and project and region):
        return None
    parent = f"projects/{project}/locations/{region}/jobs/{job}"
    lister = list_executions or _default_list_executions
    for ex in lister(parent):
        short = ex.name.rsplit("/", 1)[-1]
        if short == execution:
            continue
        # 끝난 실행은 completion_time 이 있다. 없으면 pending 이든 running 이든 활성이다 —
        # running_count 를 보면 태스크가 아직 안 뜬 실행을 놓친다 (#325 최종 리뷰, #326).
        if getattr(ex, "completion_time", None) is None:
            return short
    return None


def from_env() -> str | None:
    return another_execution_running(
        job=os.environ.get("CLOUD_RUN_JOB"),
        execution=os.environ.get("CLOUD_RUN_EXECUTION"),
        project=os.environ.get("DAENGS_GCP_PROJECT"),
        region=os.environ.get("DAENGS_GCP_REGION"),
    )


__all__ = ["another_execution_running", "from_env"]
