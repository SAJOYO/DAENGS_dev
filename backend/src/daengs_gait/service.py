"""보행 분석 HTTP 서비스 — 이 패키지의 진입점.

    uv run --no-sync gait-serve --host 0.0.0.0 --port 8000
    uv run --no-sync python -m daengs_gait.service --host 0.0.0.0 --port 8000

원본 walk_demo 는 표준 라이브러리 `ThreadingHTTPServer` 로 만든 데모 어댑터였습니다.
여기서는 FastAPI 로 다시 감쌌습니다 — 스크리닝(`skin-screening/serve.py`)과 같은 모양이라
nginx 뒤에 붙는 방식과 운영 절차가 같습니다.

⚠️ **이 앱은 `daengs_backend` 프로세스에 붙지 않습니다** (D-038). 코드와 의존성만
   backend 로 통합했고 런타임은 그대로 갈라 둡니다 — compose 의 `gait-analysis` 서비스가
   이 모듈을 자기 프로세스로 띄웁니다. 그래서 `daengs_backend` 쪽에 라우터도 서비스
   접점도 두지 않습니다. 여기에 `daengs_backend` 를 import 하지 마세요. 그 순간
   격리가 깨지고 D-029 가 지키려던 것(추론이 넘어질 때 로그인·`/ask` 까지 넘어지지
   않는 것)이 사라집니다.

⚠️ **인증이 없습니다.** 스크리닝과 같은 상태이고, 그래서 compose profile 뒤에 꺼둔 채로
   들어옵니다. 켜는 순간 `daengback/gait/` 가 인증 없는 업로드 엔드포인트가 됩니다 —
   켜는 시점은 사람이 정합니다 (D-024 와 같은 판단).

⚠️ **추론이 오래 걸립니다.** 사진 한 장이 아니라 영상 전체를 5fps 로 훑으므로 분 단위가
   될 수 있습니다. nginx 의 `proxy_read_timeout` 을 그만큼 늘려 두어야 합니다.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel

from daengs_gait import config


def _reject_if_too_large(content_length: str | None, actual_bytes: int) -> None:
    """413 처리.

    `Content-Length` 헤더로 먼저 보고(있으면 본문을 읽기 전에 거절), 헤더가 없거나
    틀린 경우를 대비해 실제로 읽은 바이트 수로 다시 확인합니다 — skin-screening 의
    "읽고 나서 검사" 방식과 같은 이중 확인입니다.
    """
    limit = config.MAX_UPLOAD_BYTES
    declared = int(content_length) if content_length and content_length.isdigit() else None
    size = max(declared or 0, actual_bytes)
    if size > limit:
        raise HTTPException(
            status_code=413,
            detail=(
                f"영상이 너무 큽니다 ({size / 1e6:.1f}MB > "
                f"{limit / 1e6:.0f}MB). 촬영 시간을 줄이거나 해상도를 낮춰 다시 "
                "업로드해 주세요."
            ),
        )


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
        request: Request,
        video: UploadFile = File(...),
        date: str | None = Form(None),
        note: str | None = Form(None),
        dog_id: str | None = Form(None),
    ):
        """영상 하나 → 보행 기록.

        ⚠️ `dog_id` 는 넘어온 값을 그대로 신뢰합니다. 두 기록이 정말 같은 개인지 검증하는
           로직이 없습니다 — 계정·반려견 프로필과 엮는 것은 아직 정하지 않았습니다.
        """
        # ⚠️ **크기·빈 파일 검사가 import 보다 먼저입니다.** 아래 두 모듈은 torch·
        #    ultralytics 를 끌고 오는데(`--extra model`), 거절할 요청 때문에 그것을
        #    올릴 이유가 없습니다. 순서를 되돌리면 기본 설치(`uv sync`, torch 없음)에서
        #    413 이어야 할 응답이 ImportError 로 바뀝니다 — 테스트가 지키고 있습니다.
        #
        # 본문을 다 읽기 전에 Content-Length 로 먼저 거절할 수 있으면 거절합니다.
        _reject_if_too_large(request.headers.get("content-length"), 0)

        content = await video.read()
        if not content:
            raise HTTPException(status_code=400, detail="빈 파일입니다.")
        _reject_if_too_large(None, len(content))

        from daengs_gait.pipeline import process_video
        from daengs_gait.video_intake import save_upload

        try:
            saved_path = save_upload(content, video.filename or "upload.mp4")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=400, detail=f"영상을 읽을 수 없습니다: {exc}"
            ) from exc

        # ⚠️ **`process_video` 를 이 코루틴 안에서 그냥 부르면 안 됩니다.** 영상 전체를
        #    훑는 동기 CPU 작업이라 분 단위가 걸리는데, 그동안 uvicorn 의 이벤트 루프가
        #    통째로 묶여 두 번째 분석은 물론 `/healthz` 와 기록 조회까지 응답이 안 나갑니다
        #    (헬스 프로브에는 서비스가 죽은 것으로 보입니다). 원본 walk_demo 는
        #    ThreadingHTTPServer 라 요청마다 스레드가 붙어 이 문제가 없었으므로, 이것은
        #    FastAPI 로 옮기면서 생긴 회귀입니다.
        #
        #    핸들러는 `async def` 로 둡니다 — 위의 `await video.read()` 가 필요해서입니다.
        #    무거운 호출만 스레드로 뺍니다.
        try:
            record = await run_in_threadpool(
                process_video,
                saved_path,
                date=date,
                note=note,
                dog_id=dog_id,
                original_filename=video.filename,
            )
        except FileNotFoundError as exc:
            # 가중치가 없는 경우입니다. 요청이 틀린 게 아니라 환경이 덜 갖춰진 것이라 503 입니다
            # (backend 의 `/ask` 가 ml 그룹 없을 때 503 을 내는 것과 같은 규칙).
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        return record

    @app.get("/v1/records/{record_id}")
    def get_record(record_id: str):
        from daengs_gait.record_store import load_record, record_exists

        if not record_exists(record_id):
            raise HTTPException(status_code=404, detail="기록을 찾을 수 없습니다.")
        return load_record(record_id)

    @app.post("/v1/compare")
    def compare(req: CompareRequest):
        """같은 개체의 두 기록 비교.

        ⚠️ 응답의 `_dev_only_*` 필드는 **UI 에 노출하면 안 됩니다.**
        """
        from daengs_gait.pipeline import compare_records
        from daengs_gait.record_store import record_exists

        for rid in (req.record_id_a, req.record_id_b):
            if not record_exists(rid):
                raise HTTPException(status_code=404, detail=f"기록을 찾을 수 없습니다: {rid}")
        return compare_records(req.record_id_a, req.record_id_b)

    @app.get("/v1/records/{record_id}/overlay")
    def get_overlay(record_id: str):
        """분석 결과를 그린 영상. 원본보다 느리게 재생됩니다(5fps 로 서브샘플하므로)."""
        from daengs_gait.record_store import load_record, record_exists

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
