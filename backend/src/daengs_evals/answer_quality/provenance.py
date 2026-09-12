"""결과 파일이 "무엇을 쟀나" 를 스스로 말하게 — 소스 SHA · dirty · 패키지 버전 (#277).

`daengs_evals.eval_harness` 의 규칙을 그대로 쓴다: 결과 파일의 meta 행에 소스 커밋과
작업트리 dirty 여부를 적고, 리포트는 그 값을 옮겨 적는다. 여기 관심 있는 패키지는 라우터 ·
판정기 경로의 것뿐이다.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from daengs_evals.eval_harness import dirty_tracked_files as _dirty_tracked_files
from daengs_evals.eval_harness import git as _git
from daengs_evals.eval_harness import package_version as _package_version

RELEVANT_PACKAGES = ("google-genai", "langgraph", "pydantic")


def source_provenance() -> dict[str, Any]:
    return {
        "source_sha": _git("rev-parse", "HEAD"),
        "source_dirty_files": _dirty_tracked_files(),
        "dev_source_sha": _git("merge-base", "HEAD", "origin/dev"),
        "packages": {name: _package_version(name) for name in RELEVANT_PACKAGES},
    }


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


__all__ = ["RELEVANT_PACKAGES", "source_provenance", "utc_now"]
