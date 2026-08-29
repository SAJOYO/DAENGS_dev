"""보행 분석 HTTP 서비스.

    uv run --no-sync python serve.py --host 0.0.0.0 --port 8000

원본 walk_demo 는 표준 라이브러리 `ThreadingHTTPServer` 로 만든 데모 어댑터였습니다.
여기서는 FastAPI 로 다시 감쌌습니다 — 스크리닝(`skin-screening/serve.py`)과 같은 모양이라
nginx 뒤에 붙는 방식과 운영 절차가 같습니다.

⚠️ **인증이 없습니다.** 스크리닝과 같은 상태이고, 그래서 compose profile 뒤에 꺼둔 채로
   들어옵니다. 켜는 순간 `daengback/gait/` 가 인증 없는 업로드 엔드포인트가 됩니다 —
   켜는 시점은 사람이 정합니다 (D-024 와 같은 판단).

⚠️ **추론이 오래 걸립니다.** 사진 한 장이 아니라 영상 전체를 5fps 로 훑으므로 분 단위가
   될 수 있습니다. nginx 의 `proxy_read_timeout` 을 그만큼 늘려 두어야 합니다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# `from src import ...` 로 부르기 위해 이 폴더를 경로에 넣습니다.
# skin-screening 과 같은 레이아웃입니다 (설치 대상 패키지가 아닙니다).
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, File, Form, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from src import config  # noqa: E402


class CompareRequest(BaseModel):
    record_id_a: str
    record_id_b: str


def build_app() -> FastAPI:
    app = FastAPI(
        title="DAENGS 보행 분석",
        description=(
            "강아지 보행 영상에서 관절 움직임을 기록하고, 같은 개체의 이전 기록과 "
            "비교합니다. **진단이 아닙니다** — 시간에 따른 변화를 관찰하는 기능입니다."
        ),
    )

    @app.get("/healthz")
    def healthz():
        """가중치가 실제로 있는지까지 봅니다.

        모델을 올리지는 않습니다 — 파일 존재만 확인합니다. 없으면 `ready: false` 로
        내려서, 컨테이너는 떴는데 가중치를 안 물린 상태를 조용히 넘기지 않습니다.
        """
        pose_ok = config.POSE_WEIGHTS.exists()
        detector_ok = config.DETECTOR_WEIGHTS.exists()
        return {
            "status": "ok",
            "ready": pose_ok and detector_ok,
            "weights": {
                "pose": {"path": str(config.POSE_WEIGHTS), "found": pose_ok},
                "detector": {"path": str(config.DETECTOR_WEIGHTS), "found": detector_ok},
            },
            "gait_filter_version": config.GAIT_FILTER_VERSION,
        }

    @app.post("/v1/analyze")
    async def analyze(
        video: UploadFile = File(...),
        date: str | None = Form(None),
        note: str | None = Form(None),
        dog_id: str | None = Form(None),
    ):
        """영상 하나 → 보행 기록.

        ⚠️ `dog_id` 는 넘어온 값을 그대로 신뢰합니다. 두 기록이 정말 같은 개인지 검증하는
           로직이 없습니다 — 계정·반려견 프로필과 엮는 것은 아직 정하지 않았습니다.
        """
        from src.pipeline import process_video
        from src.video_intake import save_upload

        content = await video.read()
        if not content:
            raise HTTPException(status_code=400, detail="빈 파일입니다.")

        try:
            saved_path = save_upload(content, video.filename or "upload.mp4")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=400, detail=f"영상을 읽을 수 없습니다: {exc}"
            ) from exc

        try:
            record = process_video(saved_path, date=date, note=note, dog_id=dog_id)
        except FileNotFoundError as exc:
            # 가중치가 없는 경우입니다. 요청이 틀린 게 아니라 환경이 덜 갖춰진 것이라 503 입니다
            # (backend 의 `/ask` 가 ml 그룹 없을 때 503 을 내는 것과 같은 규칙).
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        # 사용자가 올린 원본 이름을 남깁니다 (저장은 uuid 이름으로 했습니다).
        record["source_file"] = video.filename
        return record

    @app.get("/v1/records/{record_id}")
    def get_record(record_id: str):
        from src.record_store import load_record, record_exists

        if not record_exists(record_id):
            raise HTTPException(status_code=404, detail="기록을 찾을 수 없습니다.")
        return load_record(record_id)

    @app.post("/v1/compare")
    def compare(req: CompareRequest):
        """같은 개체의 두 기록 비교.

        ⚠️ 응답의 `_dev_only_*` 필드는 **UI 에 노출하면 안 됩니다.**
        """
        from src.pipeline import compare_records
        from src.record_store import record_exists

        for rid in (req.record_id_a, req.record_id_b):
            if not record_exists(rid):
                raise HTTPException(status_code=404, detail=f"기록을 찾을 수 없습니다: {rid}")
        return compare_records(req.record_id_a, req.record_id_b)

    @app.get("/v1/records/{record_id}/overlay")
    def get_overlay(record_id: str):
        """분석 결과를 그린 영상. 원본보다 느리게 재생됩니다(5fps 로 서브샘플하므로)."""
        from src.record_store import load_record, record_exists

        if not record_exists(record_id):
            raise HTTPException(status_code=404, detail="기록을 찾을 수 없습니다.")
        rec = load_record(record_id)
        overlay = rec.get("overlay_video")
        if not overlay or not Path(overlay).exists():
            raise HTTPException(status_code=404, detail="overlay 영상이 없습니다.")
        return FileResponse(overlay, media_type="video/mp4")

    return app


app = build_app()


def main(argv=None):
    parser = argparse.ArgumentParser(description="DAENGS 보행 분석 서비스")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--release",
        help="가중치 폴더 (GAIT_RELEASE_DIR 환경변수와 같은 역할)",
    )
    args = parser.parse_args(argv)

    if args.release:
        import os

        os.environ["GAIT_RELEASE_DIR"] = args.release
        # config 는 import 시점에 경로를 읽으므로 다시 읽혀야 합니다.
        import importlib

        importlib.reload(config)

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
