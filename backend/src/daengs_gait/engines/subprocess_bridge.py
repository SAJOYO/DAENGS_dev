"""v4 엔진 — 워커 자신의 인터프리터로 `daengs_gait.inference` 를 서브프로세스로 (D-063 5B).

5B 전에는 `backend/gait_v4/` 가 **별도 venv**(`gait-v4` 그룹, `GAIT_V4_PYTHON`)에서 돌았습니다.
이제 v4 코드는 이 패키지 안(`daengs_gait.inference`)에 있고 의존성도 `gait` 그룹 하나라,
**워커가 자기 자신(`sys.executable`)을 `-m daengs_gait.inference` 로 다시 부릅니다.**
`GAIT_V4_DIR` · `GAIT_V4_PYTHON` · `resolve_dir` · `resolve_python` 은 전부 없어졌습니다.

서브프로세스를 **유지하는** 이유(venv 를 합쳤어도 그대로 유효): ① 워커 프로세스에 torch·
rtmlib·onnxruntime 을 안 올립니다 — 장수 프로세스에 무거운 라이브러리를 쌓지 않고
② 추론이 죽어도 워커는 살아 FAILED 사유를 남깁니다 ③ 호출마다 모델 로드 ≈4s 는 분 단위
분석에 비해 무시할 만합니다.

`cwd` 는 더 이상 의미가 없습니다 — `daengs_gait` 는 venv 에 editable 설치돼 있어 어느
디렉터리에서 돌려도 같은 패키지를 찾습니다(5B 재점검에서 실측). 그래도 입력 파일이 있는
임시 디렉터리를 cwd 로 넘겨 자식이 상대 경로로 뭘 남겨도 그 안에 남게 합니다.

명령·timeout·오류 문구·`record.json` 읽기는 옛 `engines/v4.py` 와 같습니다.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from daengs_gait.contract import AnalysisRecord

#: 60초 영상 ≈58s(CPU) 에 모델 로드 4s. 여유를 크게 둡니다 — 워커의 다른 상한
#: (Celery soft time limit) 이 있으면 그쪽이 먼저입니다.
V4_TIMEOUT_SECONDS = 20 * 60

#: 자식이 실행할 모듈. `python -m daengs_gait.inference analyze …`.
INFERENCE_MODULE = "daengs_gait.inference"


class SubprocessBridgeEngine:
    name = "v4"

    def __init__(self, *, timeout_seconds: int | None = None, python: str | Path | None = None) -> None:
        self.timeout_seconds = timeout_seconds or V4_TIMEOUT_SECONDS
        # 테스트가 가짜 인터프리터를 꽂을 자리. 운영에서는 항상 자기 자신입니다.
        self.python = Path(python) if python else Path(sys.executable)

    def analyze(self, local_path: Path) -> AnalysisRecord:
        """`python -m daengs_gait.inference analyze` 를 돌리고 record JSON 을 읽습니다.

        반환 dict 는 legacy `process_video` 와 **같은 키**를 갖습니다 — `quality` ·
        `features.summary_for_ui` · `features.internal_feature_vector` · `gait_filter_version` ·
        `video_meta` · `pose_model` · `overlay_video`(경로). 그래서 `_run_analysis` 는 엔진을 모릅니다.

        `follow_cam` 은 앱 계약(`GaitAnalyzeRequest`)에 없어 **False 고정**입니다 — legacy 와
        같이 정지 구간 필터가 켜집니다. 촬영 가이드에서 사용자가 고르게 되면 그때 받습니다.
        """
        local = local_path if hasattr(local_path, "parent") else Path(local_path)
        out = local.parent / "record.json"
        overlay = local.parent / "overlay.mp4"
        cmd = [
            str(self.python),
            "-m",
            INFERENCE_MODULE,
            "analyze",
            str(local),
            "--overlay",
            str(overlay),
            "--out",
            str(out),
        ]
        proc = subprocess.run(
            cmd,
            cwd=str(local.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout_seconds,
            check=False,  # 실패는 아래에서 stderr 꼬리를 붙여 우리 예외로 바꿉니다
        )
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-2000:]
            raise RuntimeError(f"v4 분석 실패 (exit {proc.returncode}): {tail}")
        if not out.exists():
            raise RuntimeError("v4 추론이 record.json 을 만들지 않았습니다.")
        record = json.loads(out.read_text(encoding="utf-8"))
        # quality 가 ok 가 아니면 analyze_video 가 overlay 를 만들지 않습니다 — 키가 없거나
        # 파일이 없으면 호출자(`_analyze_from_storage`)가 overlay 없음으로 처리합니다.
        if record.get("overlay_video") and not overlay.exists():
            record["overlay_video"] = None
        return record
