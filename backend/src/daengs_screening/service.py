"""반려견 피부 스크리닝 HTTP 경계 — backend 프로세스 안의 라우터.

앱(DAENGS_APP)이 부르는 곳입니다. 판정 로직은 `agent.py` 에 있고 여기는 HTTP 만 봅니다.

    POST /screen/v1/screen    multipart 사진 한 장 → 판정 JSON
    GET  /screen/healthz      살아있나 + 어떤 가중치를 물고 있나
    GET  /screen/             데모 화면 (개발용)

**앱이 부르는 주소는 예전 그대로입니다.** 최상위 `skin-screening` 컨테이너에서
backend 로 옮겨 오면서 nginx 의 upstream 만 바뀌었고, 경로는 안 바뀌었습니다 (D-039).

⚠️ `from __future__ import annotations` 를 넣지 마세요. `UploadFile` 이 문자열
   어노테이션이 되면 pydantic 이 이름을 못 찾고 **500** 으로 죽습니다.

⚠️ **인증이 없습니다.** 이건 데모에서 넘어온 코드이고 레이트 리밋·인증이 아직
   없습니다. 사진은 디스크에 저장하지 않고 메모리에서 처리한 뒤 버립니다
   (보호자 사진을 동의 없이 모으지 않기).
"""

import io
import json
import os
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

# ⚠️ **여기서 무거운 것을 import 하지 마세요.** `daengs_backend.main` 이 이 모듈을
#    import 하는데, `tests/test_main_stays_light.py` 가 torch 가 딸려 오는지
#    별도 프로세스로 검사합니다 (D-021). `agent` 는 최상단이 `config` 뿐이라
#    가볍고, torch 는 `infer.py` 안에서 함수가 부를 때 올라옵니다.
from daengs_screening.agent import CONTRACT_VERSION

MAX_BYTES = 12 * 1024 * 1024          # 휴대폰 사진 한 장이면 충분합니다
STATIC = Path(__file__).resolve().parent / "static"

#: 가중치가 있는 폴더. compose 가 `:ro` 로 물려 줍니다.
#: 저장소에는 없습니다 — best.pt 가 100MB 리밋을 넘습니다 (D-022).
RELEASE_DIR = os.environ.get("SCREENING_RELEASE_DIR", "/models/release")

router = APIRouter(prefix="/screen", tags=["screening"])


@lru_cache(maxsize=1)
def _agent():
    """가중치를 **첫 요청 때** 올립니다.

    ⚠️ 기동 때 올리면 안 됩니다. 이 프로세스에는 로그인과 `/ask` 가 같이 떠 있고
       (D-021), 350MB 를 물고 오느라 기동이 늦어지면 그쪽까지 같이 늦어집니다.
       스크리닝을 아무도 안 부르는 동안 메모리를 잡고 있을 이유도 없습니다.

    ⚠️ torch 는 **이 함수 안에서** 처음 올라옵니다. 최상단으로 끌어올리면
       `test_main_stays_light.py` 가 깨집니다.
    """
    from daengs_screening.agent import ScreeningAgent

    return ScreeningAgent.from_release(RELEASE_DIR)


def _fail(exc: Exception) -> HTTPException:
    """가중치가 없거나 못 읽을 때. **503 입니다** — 요청이 잘못된 게 아닙니다."""
    return HTTPException(
        503,
        "스크리닝 모델을 불러오지 못했습니다. 서버에 가중치가 놓여 있는지 "
        f"확인하세요 (SCREENING_RELEASE_DIR={RELEASE_DIR}). 원인: {exc}",
    )


@router.get("/healthz")
def healthz():
    """살아있나 + 어떤 가중치를 물고 있나.

    ⚠️ 여기서 모델을 올리지 않습니다. 올라와 있으면 임계값을 같이 알려주고,
       아직이면 `loaded: false` 로 답합니다 — 헬스체크가 350MB 를 끌어오면 안 됩니다.
    """
    loaded = _agent.cache_info().currsize > 0
    body = {"ok": True, "mock": False, "contract_version": CONTRACT_VERSION,
            "loaded": loaded, "release_dir": RELEASE_DIR}
    if loaded:
        body["threshold"] = getattr(_agent(), "thr", None)
    return body


@router.post("/v1/screen")
async def screen(photo: UploadFile = File(...), box: str = Form(default="")):
    """box: 앱의 **가이드 프레임**. 정규화 JSON `[x, y, w, h]` (0~1).

    주면 학습과 같은 함수로 자릅니다 (`agent.crop_for`). 안 주면 화면 중앙으로
    물러섭니다 — 1단계는 큰 차이가 없지만 2단계는 학습 크롭과 어긋납니다.
    """
    from PIL import Image

    raw = await photo.read()
    if not raw:
        raise HTTPException(400, "빈 파일입니다.")
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, f"사진이 너무 큽니다 ({len(raw) / 1e6:.1f}MB > 12MB).")
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception:
        raise HTTPException(415, "이미지로 열리지 않는 파일입니다.")

    b = None
    if box:
        try:
            b = json.loads(box)
            if not (isinstance(b, list) and len(b) == 4):
                raise ValueError
            b = [float(v) for v in b]
        except Exception:
            raise HTTPException(422, "box 는 정규화 [x, y, w, h] JSON 배열이어야 합니다.")

    try:
        agent = _agent()
    except Exception as exc:                       # 가중치 없음 · 손상
        raise _fail(exc) from exc

    # ⚠️ 사진 한 장에 CPU 로 0.6~3초입니다. 이벤트 루프를 막으면 그동안 로그인도
    #    `/ask` 도 멈춥니다 — 같은 프로세스이기 때문입니다 (D-039).
    from starlette.concurrency import run_in_threadpool

    return JSONResponse(await run_in_threadpool(agent.screen, im, b))


@router.get("/")
def index():
    """개발용 데모 화면.

    ⚠️ 이 화면은 `fetch('/healthz')` 처럼 **오리진 루트**를 부르므로 `/screen/`
       아래에서는 진단이 안 돕니다. 원본 저장소의 사본이라 고치지 않습니다
       (D-022). 화면을 제대로 보려면 원본에서 `serve.py --mock` 으로 띄우세요.
    """
    demo = STATIC / "index.html"
    if not demo.exists():
        return {"hint": "데모 화면이 없습니다. POST /screen/v1/screen 을 직접 쓰세요."}
    return FileResponse(demo)
