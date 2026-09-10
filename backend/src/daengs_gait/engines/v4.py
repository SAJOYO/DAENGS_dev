"""walk_demo v4 엔진 (#304) — 별도 venv 의 서브프로세스.

`backend/gait_v4/` 코드는 **자기 venv**(backend lock 의 `gait-v4` 그룹, D-063 5A)에서 돕니다.
워커 venv 에는 설치되지 않으므로 import 할 수 없고, 그 venv 의 python 을 서브프로세스로
부릅니다. 그래서 얻는 것 — ① 골든이 나온 버전 조합(torch 2.13.0 · numpy 2.5.2 …, `==` 핀)을
그대로 두고 ② 라이선스 결정 전 가중치가 운영 이미지에 들어가지 않으며 ③ 이 프로세스에 torch 가
안 올라옵니다. 대가는 호출마다 모델 로드 ≈4s 인데, 분석 자체가 분 단위라 무시할 만합니다.
(5A 전에는 그 폴더가 자기 pyproject·uv.lock 을 가진 별도 uv 프로젝트였습니다.)

`daengs_backend.services.gait` 에 있던 `_v4_dir` · `_v4_python` · `_analyze_with_v4` 를
그대로 옮긴 것입니다 (D-063 2단계). 명령·cwd·timeout·오류 문구·record.json 처리는 같습니다.
설정값(`GAIT_V4_DIR` · `GAIT_V4_PYTHON`)은 backend 가 **인자로** 넘깁니다 — 이 모듈은
`daengs_backend` 를 import 하지 않습니다.
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


def resolve_dir(configured: str | Path | None) -> Path:
    """gait_v4 프로젝트 폴더. 설정이 비면 저장소의 `backend/gait_v4`."""
    if configured:
        return Path(configured)
    # engines/v4.py → engines → daengs_gait → src → backend. 그 밑의 gait_v4.
    return Path(__file__).resolve().parents[3] / "gait_v4"


def resolve_python(configured: str | Path | None, root: Path) -> Path:
    """v4 venv 의 python. 설정(`GAIT_V4_PYTHON`)이 우선 — 컨테이너는 코드 폴더가 :ro 라
    venv 를 /opt 에 두고 이 값으로 알려 줍니다. 없으면 `<root>/.venv/…/python`."""
    if configured:
        exe = Path(configured)
        if not exe.exists():
            raise RuntimeError(
                f"GAIT_V4_PYTHON 이 가리키는 python 이 없습니다: {exe} — 컨테이너면 command 의 "
                "v4 `uv sync` 가 돌았는지 로그를 보세요."
            )
        return exe
    exe = root / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not exe.exists():
        # 5A 부터 v4 의존성은 backend lock 의 `gait-v4` 그룹입니다 — 그 폴더에는 자기 pyproject 가 없습니다.
        raise RuntimeError(
            f"gait_v4 venv 가 없습니다: {exe} — backend 에서 "
            f"`UV_PROJECT_ENVIRONMENT={root / '.venv'} uv sync --only-group gait-v4 --no-install-project` "
            "를 먼저 하세요."
        )
    return exe


class V4Engine:
    name = "v4"

    def __init__(
        self,
        *,
        configured_dir: str | Path | None = None,
        configured_python: str | Path | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        self.configured_dir = configured_dir
        self.configured_python = configured_python
        self.timeout_seconds = timeout_seconds or V4_TIMEOUT_SECONDS

    @property
    def root(self) -> Path:
        return resolve_dir(self.configured_dir)

    @property
    def python(self) -> Path:
        return resolve_python(self.configured_python, self.root)

    def analyze(self, local_path: Path) -> AnalysisRecord:
        """`python -m gait_v4 analyze` 를 돌리고 record JSON 을 읽습니다.

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
            "gait_v4",
            "analyze",
            str(local),
            "--overlay",
            str(overlay),
            "--out",
            str(out),
        ]
        proc = subprocess.run(
            cmd,
            cwd=str(self.root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout_seconds,
            check=False,  # 실패는 아래에서 stderr 꼬리를 붙여 우리 예외로 바꿉니다
        )
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-2000:]
            raise RuntimeError(f"gait_v4 분석 실패 (exit {proc.returncode}): {tail}")
        if not out.exists():
            raise RuntimeError("gait_v4 가 record.json 을 만들지 않았습니다.")
        record = json.loads(out.read_text(encoding="utf-8"))
        # quality 가 ok 가 아니면 analyze_video 가 overlay 를 만들지 않습니다 — 키가 없거나
        # 파일이 없으면 호출자(`_analyze_from_storage`)가 overlay 없음으로 처리합니다.
        if record.get("overlay_video") and not overlay.exists():
            record["overlay_video"] = None
        return record
