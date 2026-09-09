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
import threading
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

#: 허깅페이스 리포에서 받아 옵니다. **비어 있으면 예전처럼 위 폴더를 봅니다** —
#: 되돌리기가 환경 변수 하나이고, 토큰 없는 개발 PC 도 그대로 돕니다.
RELEASE_REPO = os.environ.get("SCREENING_RELEASE_REPO", "").strip()

#: ⚠️ **반드시 고정하세요.** 비우면 `main` 을 따라가므로, 리포에 푸시하는 순간
#: 운영 모델이 바뀝니다 — PR 도 리뷰도 배포도 없이. `.env` 에 태그를 적어 두면
#: 모델 교체가 **리뷰되는 행위**가 됩니다.
RELEASE_REVISION = os.environ.get("SCREENING_RELEASE_REVISION", "").strip() or None


def _expect_arms() -> int | None:
    """기대하는 2단계 팔 수. 비어 있거나 숫자가 아니면 검사하지 않습니다.

    `SCREENING_EXPECT_STAGE2_ARMS` 가 비어 있으면 **리포에서 받는 구성일 때만**
    3 을 기대합니다. 폴더를 쓰는 개발 PC 는 1팔짜리로도 돌아야 하기 때문입니다.

    ⚠️ compose 의 `environment:` 에 넣지 마세요 — `backend/.env` 자리입니다
       (`HF_TOKEN` 이 거기서 덮이는 것과 같은 이유).
    """
    raw = os.environ.get("SCREENING_EXPECT_STAGE2_ARMS", "").strip()
    if raw:
        return int(raw) if raw.isdigit() else None
    # 안 정했으면 — 리포에서 받아 오는 구성일 때만 3 을 기대합니다.
    # 폴더를 쓰는 개발 PC 는 1팔짜리 릴리스로도 돌 수 있어야 합니다.
    return 3 if RELEASE_REPO else None


#: 다운로드는 한 번만 합니다. `_agent()` 는 `run_in_threadpool` 로 불리므로
#: **첫 요청 둘이 동시에 오면 두 스레드가 같이 들어옵니다** — `lru_cache` 는
#: 그것을 막아 주지 않습니다.
_download_lock = threading.Lock()
_downloaded: str | None = None


def _release_path(download: bool) -> str | None:
    """가중치 폴더의 **실제** 경로.

    `SCREENING_RELEASE_REPO` 가 없으면 예전 그대로 `RELEASE_DIR` 입니다.

    있으면 허깅페이스에서 받습니다. **`local_dir` 을 주지 않는 것이 중요합니다** —
    `/models/release` 는 compose 가 `:ro` 로 물려 주므로 거기에 쓰면 실패합니다.
    `snapshot_download` 는 이미 쓰기 가능하게 물려 있는 `hf-cache` 볼륨
    (`/root/.cache/huggingface`)에 받고 **스냅샷 폴더 경로**를 돌려주므로,
    compose 를 안 고쳐도 됩니다.

    ⚠️ *"폴더가 비어 있으면 받는다"* 로 하지 마세요. 반쯤 찬 폴더(받다 끊긴 것,
       손으로 복사해 둔 옛 릴리스)는 *"안 비었네"* 로 통과하고 **팔이 모자란 채
       200 을 돌려줍니다.** 그래서 늘 부릅니다 — 최신이면 HEAD 한 번입니다.

    Args:
        download: False 면 **이미 받아 둔 것만** 봅니다. `/healthz` 가 1.2GB 를
            끌어오면 안 되기 때문입니다 (헬스체크는 무거우면 안 됩니다).
    """
    global _downloaded

    if not RELEASE_REPO:
        return RELEASE_DIR
    if _downloaded:
        return _downloaded

    from huggingface_hub import snapshot_download

    if not download:
        try:
            return snapshot_download(repo_id=RELEASE_REPO, revision=RELEASE_REVISION,
                                     local_files_only=True)
        except Exception:  # noqa: BLE001 — 헬스체크는 **어떤 이유로도** 죽으면 안 됩니다
            return None            # 아직 안 받았습니다 — 헬스체크는 그렇게 답합니다

    with _download_lock:
        if _downloaded is None:
            _downloaded = snapshot_download(repo_id=RELEASE_REPO,
                                            revision=RELEASE_REVISION)
    return _downloaded

router = APIRouter(prefix="/screen", tags=["screening"])


@lru_cache(maxsize=1)
def _agent():
    """가중치를 **첫 요청 때** 올립니다.

    ⚠️ 기동 때 올리면 안 됩니다. 이 프로세스에는 로그인과 `/life/ask` 가 같이 떠 있고
       (D-021), 350MB 를 물고 오느라 기동이 늦어지면 그쪽까지 같이 늦어집니다.
       스크리닝을 아무도 안 부르는 동안 메모리를 잡고 있을 이유도 없습니다.

    ⚠️ torch 는 **이 함수 안에서** 처음 올라옵니다. 최상단으로 끌어올리면
       `test_main_stays_light.py` 가 깨집니다.
    """
    from daengs_screening.agent import ScreeningAgent

    path = _release_path(download=True)

    # ★ 팔 수가 안 맞으면 **여기서 죽습니다.** 2026-09-07 에 앙상블이 조용히
    #   1팔로 줄어든 적이 있는데, 응답은 200 이었고 에러도 경고도 없었습니다 —
    #   유일한 단서가 **성공 로그의 부재**였습니다. 그때 커버리지가 67.9% 에서
    #   58.4% 로 떨어졌고 아무도 몰랐습니다.
    want = _expect_arms()
    if want is not None:
        arms = _arms_on_disk()
        if len(arms) != want:
            raise RuntimeError(
                f"2단계 팔이 {len(arms)}개입니다 — {want}개를 기대했습니다 "
                f"(SCREENING_EXPECT_STAGE2_ARMS). 릴리스: {path} / "
                f"있는 것: {arms or '(없음)'}"
            )

    return ScreeningAgent.from_release(path)


def _fail(exc: Exception) -> HTTPException:
    """가중치가 없거나 못 읽을 때. **503 입니다** — 요청이 잘못된 게 아닙니다."""
    return HTTPException(
        503,
        "스크리닝 모델을 불러오지 못했습니다. 서버에 가중치가 놓여 있는지 "
        f"확인하세요 (SCREENING_RELEASE_REPO={RELEASE_REPO or '(없음)'} / "
        f"SCREENING_RELEASE_DIR={RELEASE_DIR}). 원인: {exc}",
    )


def _arms_on_disk() -> list[str]:
    """릴리스 폴더에 **준비된** 2단계 팔 이름. 모델은 안 올립니다.

    `ScreeningAgent.from_release()` 가 `stage2_*` 폴더를 전부 훑어 앙상블을
    구성하므로, 폴더만 세어도 몇 팔로 뜰지 알 수 있습니다.
    """
    path = _release_path(download=False)
    if path is None:
        return []                  # 리포는 정해졌는데 아직 안 받았습니다
    ck = Path(path) / "checkpoints"
    if not ck.is_dir():
        return []
    return sorted(d.name for d in ck.iterdir()
                  if d.is_dir() and d.name.startswith("stage2_")
                  and (d / "best.pt").exists())


@router.get("/healthz")
def healthz():
    """살아있나 + **어떤 가중치를 몇 팔로** 물고 있나.

    ⚠️ 여기서 모델을 올리지 않습니다. 올라와 있으면 임계값을 같이 알려주고,
       아직이면 `loaded: false` 로 답합니다 — 헬스체크가 350MB 를 끌어오면 안 됩니다.

    ★ **팔 개수를 말하는 이유** — 배포에서 앙상블이 조용히 1팔로 줄어든 적이
    있습니다 (2026-09-07). `POST /screen/v1/screen` 응답의 `meta.stage2_arms`
    로도 알 수 있지만 그건 **2단계가 실제로 도는 사진**이 있어야 나옵니다.
    실제로 확인하는 데 찌르기 6번과 합성 병변 사진 한 장이 들었습니다.

    두 가지를 나눠서 냅니다:

        stage2_arms_available   릴리스 폴더에 **준비된** 팔 (모델 안 올려도 나옴)
        stage2_arms             지금 **올라와 있는** 팔 (loaded 일 때만)

    둘이 다르면 릴리스는 새것인데 프로세스가 옛것을 물고 있다는 뜻입니다 —
    재시작하면 맞습니다.
    """
    loaded = _agent.cache_info().currsize > 0
    avail = _arms_on_disk()
    body = {"ok": True, "mock": False, "contract_version": CONTRACT_VERSION,
            "loaded": loaded,
            "release_dir": _release_path(download=False) or RELEASE_DIR,
            "release_repo": RELEASE_REPO or None,
            "release_revision": RELEASE_REVISION,
            "stage2_arms_available": len(avail),
            "stage2_experiments_available": avail}
    if loaded:
        ag = _agent()
        body["threshold"] = getattr(ag, "thr", None)
        try:
            body.update(ag.describe())     # 상류(deeplearning_test)와 같은 키
        except AttributeError:             # 상류가 옛 버전이면 조용히 물러섭니다
            arms = getattr(ag, "arms2", None)
            body["stage2_arms"] = len(arms) if arms else 1
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
    #    `/life/ask` 도 멈춥니다 — 같은 프로세스이기 때문입니다 (D-039).
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
