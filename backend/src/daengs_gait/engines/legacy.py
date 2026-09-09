"""legacy 엔진 — YOLO `best.pt` 12 관절 + crop-assist, 이 프로세스 안에서 (D-029 · D-038).

`daengs_gait.pipeline` 이 torch·ultralytics 를 끌고 오므로 **함수 안에서 지연 import** 합니다.
`get_engine("legacy")` 를 부르는 워커에만 그것이 깔려 있고, backend 웹 프로세스는 이 모듈에
닿지 않습니다 (`tests/test_gait_app_api.py::test_task_module_imports_without_gait_deps`).
"""

from __future__ import annotations

from pathlib import Path

from daengs_gait.contract import AnalysisRecord


class LegacyEngine:
    name = "legacy"

    def analyze(self, local_path: Path) -> AnalysisRecord:
        from daengs_gait.pipeline import process_video  # 지연 — torch 가 여기서 올라옵니다

        # legacy HTTP 서비스는 JSON·overlay 를 GAIT_DATA_DIR 에 보존하지만, D-043 워커의
        # 원장은 PostgreSQL/storage 입니다. persist=False 로 task 임시 디렉터리 밖에
        # worker-side 사본을 만들지 않습니다.
        return process_video(local_path, persist=False)
