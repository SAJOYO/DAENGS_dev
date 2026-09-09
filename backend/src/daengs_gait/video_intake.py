"""옛 HTTP 서비스(`daengs_gait.service`)의 업로드 저장 — **판정 로직은 `intake.py` 로 옮겼습니다.**

원본: walk_demo `frontend/server.py` 의 `_ensure_mp4` · `_download_url`.

여기 남은 것은 옛 `/gait/analyze` 경로 전용입니다 (nginx 가 410 으로 닫아 두었고 4단계에서
통째로 지웁니다):
  1. `save_upload` — bytes 를 `UPLOADS_DIR` 에 uuid 이름으로 저장하고 `intake.prepare_for_analysis`
     와 같은 규칙(읽히면 원본, 아니면 변환, 그래도 안 되면 예외)으로 판정
  2. `download_url` — `yt-dlp` 로 받아옴 (선택 기능, 유지 여부 미정)

`probe_decodable` · `transcode_to_h264` · `VideoDecodeError` · `ProbeResult` 는 `intake` 의 것을
그대로 내보냅니다 — 기존 테스트가 이 모듈 이름으로 부르고 패치하기 때문입니다.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from daengs_gait.config import UPLOADS_DIR
from daengs_gait.intake import (  # noqa: F401 — 재export (옛 호출자·테스트 호환)
    PROBE_POINTS,
    ProbeResult,
    VideoDecodeError,
    probe_decodable,
    transcode_to_h264,
)

log = logging.getLogger(__name__)


def ensure_dir() -> Path:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    return UPLOADS_DIR


def ensure_mp4(path: Path) -> Path:
    """mp4 가 아니면 H.264 mp4 로 변환합니다. 이미 mp4 면 그대로 둡니다.

    ⚠️ **확장자만 봅니다.** 업로드 경로(`save_upload`)는 이 함수를 쓰지 않고
       `probe_decodable` 로 판단합니다 — 확장자는 코덱을 말해주지 않아서, `.mov`(H.264)를
       불필요하게 재인코딩하고 `.mp4`(AV1)는 못 읽는 채로 통과시켰기 때문입니다.
       여기 남아 있는 이유는 `download_url` 이 아직 씁니다 (yt-dlp 산출물의 확장자가
       무엇일지 미리 알 수 없어 컨테이너를 맞춰 두는 용도).
    """
    path = Path(path)
    if path.suffix.lower() == ".mp4":
        return path

    return transcode_to_h264(path)


def save_upload(content: bytes, filename: str) -> Path:
    """업로드 바이트를 저장하고 **분석 가능한 형태인지 확인**합니다.

    저장 이름은 uuid 입니다 — 사용자가 올린 이름을 파일 경로로 쓰면 경로 조작이 됩니다.
    원본 이름은 기록의 `source_file` 에 따로 남깁니다.

    **읽을 수 있으면 원본을 그대로 씁니다.** 예전에는 확장자가 `.mp4` 가 아니면 무조건
    재인코딩했는데, 그것이 결과를 바꿨습니다 — 같은 `IMG_8631.mov` 를 walk_demo 는 원본으로
    읽고 이쪽은 변환본으로 읽어서 detected 99→95 · usable 3→5 · blur 170→200 으로
    갈렸습니다 (2026-08-31 실측, 298 프레임 전 필드 대조로 확인).

    변환은 **읽지 못할 때만** 겁니다 — webm/vp9 처럼 cv2 빌드에 따라 불안정한 것,
    AV1 처럼 디코더가 아예 없는 것이 여기로 옵니다.

    판정 규칙은 `intake.prepare_for_analysis` 와 같습니다. 이 모듈의 `probe_decodable` ·
    `transcode_to_h264` 이름을 부르는 것은 기존 테스트가 그 이름을 패치하기 때문입니다.
    """
    ensure_dir()
    suffix = Path(filename).suffix.lower() or ".mp4"
    dest = UPLOADS_DIR / f"{uuid.uuid4().hex[:8]}{suffix}"
    dest.write_bytes(content)

    probe = probe_decodable(dest)
    if probe.ok:
        log.info(
            "[gait-intake] 원본 그대로 분석: %s  (%sx%s @%.3ffps, %s 로 %d장 확인)",
            dest,
            probe.width,
            probe.height,
            probe.native_fps,
            probe.method,
            probe.frames_read,
        )
        return dest

    log.warning(
        "[gait-intake] 원본을 읽지 못해 H.264 로 변환합니다: %s  사유=%s",
        dest,
        probe.reason,
    )
    converted = transcode_to_h264(dest)

    # ⚠️ **변환했다고 읽힌다는 보장이 없습니다.** 반드시 다시 확인합니다 — 여기를
    #    건너뛰면 못 읽는 파일이 분석으로 넘어가 `sampled: 0` 이 되고, 사용자는
    #    "다시 촬영해 주세요" 라는 **엉뚱한 안내**를 받습니다 (worklog 3-2).
    probe2 = probe_decodable(converted)
    if not probe2.ok:
        raise VideoDecodeError(
            f"이 영상 형식을 읽을 수 없습니다 (변환 후에도 실패: {probe2.reason}). "
            "H.264 로 촬영·저장된 mp4 로 다시 올려 주세요."
        )

    log.info(
        "[gait-intake] 재인코딩 후 분석: %s → %s  (%sx%s @%.3ffps, %s 로 %d장 확인)",
        dest.name,
        converted,
        probe2.width,
        probe2.height,
        probe2.native_fps,
        probe2.method,
        probe2.frames_read,
    )
    return converted


def download_url(url: str) -> tuple[Path, str | None]:
    """URL 의 영상을 받아옵니다. 반환: (경로, 제목).

    `yt-dlp` 가 병합에 쓰는 ffmpeg 는 시스템 PATH 대신 imageio-ffmpeg 내장 바이너리를
    넘겨 줍니다 (별도 설치 불필요). 일부 사이트의 봇 차단(HTTP 403)은 브라우저
    impersonation 으로 우회합니다 — `curl_cffi` 가 있어야 동작합니다.

    ⚠️ 이 함수는 선택 기능입니다. `yt-dlp` 가 없으면 ImportError 가 납니다 —
       부르는 쪽(`serve.py`)이 그것을 503 으로 옮깁니다.
    """
    import imageio_ffmpeg
    import yt_dlp
    from yt_dlp.networking.impersonate import ImpersonateTarget

    ensure_dir()
    dest = UPLOADS_DIR / f"{uuid.uuid4().hex[:8]}.mp4"
    ydl_opts = {
        # keypoint 분석에 고해상도가 필요 없어 1080p 로 캡합니다 —
        # 불필요한 대용량 다운로드와 디코딩을 막습니다.
        "format": (
            "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4][height<=1080]/best"
        ),
        "outtmpl": str(dest.with_suffix("")) + ".%(ext)s",
        "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
        "quiet": True,
        "noprogress": True,
        "impersonate": ImpersonateTarget("chrome"),
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
    title = info.get("title") if isinstance(info, dict) else None

    if not dest.exists():
        # 확장자가 다르게 떨어졌을 수 있습니다.
        candidates = sorted(UPLOADS_DIR.glob(dest.stem + ".*"))
        if not candidates:
            raise FileNotFoundError(f"영상을 받지 못했습니다: {url}")
        dest = ensure_mp4(candidates[0])

    return dest, title
