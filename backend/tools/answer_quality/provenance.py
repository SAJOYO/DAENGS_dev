"""결과 파일이 "무엇을 쟀나" 를 스스로 말하게 — 소스 SHA · dirty · 패키지 버전 (#277).

비교 v2 러너의 규칙을 그대로 빌린다: 결과 파일의 meta 행에 소스 커밋과 작업트리 dirty 여부를
적고, 리포트는 그 값을 옮겨 적는다. 여기 관심 있는 패키지는 라우터 · 판정기 경로의 것뿐이다.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from tools.orchestrator_comparison.runner_v2 import _dirty_tracked_files, _git, _package_version

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
