"""엔진이 backend 에 약속하는 것 — 영상 파일 하나를 받아 AnalysisRecord 를 돌려준다."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from daengs_gait.contract import AnalysisRecord


class Engine(Protocol):
    """분석 엔진 하나.

    `analyze` 의 반환값은 `daengs_gait.contract.AnalysisRecord` 입니다 — `quality` ·
    `features` · `gait_filter_version` · `video_meta` · `pose_model` · `overlay_video`(경로 또는
    None)가 **보장되는 최소 키**이고, 엔진이 그 밖의 키(v4 의 `timing`·`lr_fix`·`trajectories`,
    legacy 의 `original_video` 등)를 더 내는 것은 계약이 허용합니다(`contract.py` 머리말).
    `services/gait._run_analysis` 는 그 여섯 키만 읽으므로 엔진을 모릅니다.

    overlay 파일은 `local_path` 와 같은 임시 디렉터리에 만듭니다 — 호출자가 bytes 를 읽은 뒤
    디렉터리째 지웁니다. 워커 볼륨에 사본을 남기지 않습니다 (D-043).
    """

    #: `GAIT_ENGINE` 값과 같은 이름. 로그·오류 문구용.
    name: str

    def analyze(self, local_path: Path) -> AnalysisRecord: ...
