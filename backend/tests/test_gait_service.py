"""PR #62 리뷰 지적 ① — `/analyze` 가 이벤트 루프를 막지 않는다.

**가중치도 torch 도 없이 돕니다.** `daengs_gait.pipeline` 을 대역 모듈로 갈아 끼우므로
cv2·ultralytics 가 필요 없습니다 — `test_upload_limit.py` 와 같은 장치이고, 기본
설치(`uv sync`, `--group gait` 없음)에서도 이 회귀가 잡혀야 하기 때문입니다.

모델이 실제로 필요한 나머지 회귀(overlay·비교 문구·파일명)는
`test_review_fixes_model.py` 에 있습니다.
"""

from __future__ import annotations

import asyncio
import sys
import time
import types

import pytest


ANALYZE_SECONDS = 2.0


@pytest.fixture()
def stubbed_serve(monkeypatch, tmp_path):
    """`daengs_gait.pipeline` · `daengs_gait.video_intake` 를 대역으로 바꾼 app.

    대역의 `process_video` 는 **동기로 잠듭니다** — 진짜 추론이 하는 일(영상 전체를
    훑는 동기 CPU 작업)의 최소 재현입니다.
    """
    fake_pipeline = types.ModuleType("daengs_gait.pipeline")

    def slow_process_video(path, **kw):
        time.sleep(ANALYZE_SECONDS)
        return {"record_id": "deadbeef", "source_file": kw.get("original_filename")}

    fake_pipeline.process_video = slow_process_video

    fake_intake = types.ModuleType("daengs_gait.video_intake")
    fake_intake.save_upload = lambda content, filename: tmp_path / "saved.mp4"

    monkeypatch.setitem(sys.modules, "daengs_gait.pipeline", fake_pipeline)
    monkeypatch.setitem(sys.modules, "daengs_gait.video_intake", fake_intake)

    from daengs_gait import service as serve

    return serve.build_app()


def test_healthz_responds_while_analyze_is_running(stubbed_serve):
    """분석이 도는 동안 `/healthz` 가 굶으면 안 됩니다.

    옛 코드는 `async def` 핸들러 안에서 동기 CPU 작업을 그대로 불러 이벤트 루프를
    통째로 묶었습니다 — 분 단위 분석 하나가 `/healthz` 와 기록 조회까지 막아
    헬스 프로브에는 서비스가 죽은 것으로 보였습니다. 원본 walk_demo 는
    ThreadingHTTPServer 라 이 문제가 없었으므로 **이식이 만든 회귀**입니다.
    """
    import httpx

    async def scenario():
        transport = httpx.ASGITransport(app=stubbed_serve)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            analyze = asyncio.create_task(
                c.post(
                    "/analyze",
                    files={"video": ("walk_0829.mp4", b"0123456789", "video/mp4")},
                    timeout=30.0,
                )
            )
            await asyncio.sleep(0.3)  # 분석이 실제로 시작되도록 둡니다

            t0 = time.perf_counter()
            health = await c.get("/healthz", timeout=10.0)
            waited = time.perf_counter() - t0

            return health, waited, await analyze

    health, waited, analyzed = asyncio.run(scenario())

    assert health.status_code == 200
    assert analyzed.status_code == 200
    assert waited < ANALYZE_SECONDS / 2, (
        f"/healthz 가 {waited:.2f}s 걸렸습니다 — 분석이 이벤트 루프를 막고 있습니다."
    )


def test_analyze_passes_original_filename_through(stubbed_serve):
    """핸들러가 원본 파일명을 `process_video` 로 넘겨야 합니다.

    저장 뒤에 반환 dict 만 고치던 옛 방식으로 되돌아가면 이 인자가 사라집니다.
    """
    from fastapi.testclient import TestClient

    resp = TestClient(stubbed_serve).post(
        "/analyze",
        files={"video": ("walk_0829.mp4", b"0123456789", "video/mp4")},
    )
    assert resp.status_code == 200
    assert resp.json()["source_file"] == "walk_0829.mp4"
