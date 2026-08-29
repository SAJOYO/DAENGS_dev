"""업로드된 영상을 분석 가능한 형태로 들이는 단계.

원본: walk_demo `frontend/server.py` 의 `_ensure_mp4` · `_download_url`.

두 가지를 합니다:
  1. mp4/H.264 가 아니면 변환 — 브라우저 MediaRecorder 로 녹화하면 webm/vp9 가 나오는데
     cv2 빌드에 따라 디코딩이 불안정합니다. overlay 와 같은 imageio-ffmpeg 정적 바이너리로
     미리 변환해 둡니다.
  2. (선택) URL 다운로드 — `yt-dlp` 로 받아옵니다. **이 기능을 서비스에서 유지할지는
     아직 정하지 않았습니다** (README 의 TBD). 유지하지 않으면 `yt-dlp` · `curl_cffi`
     의존성을 통째로 뺄 수 있어서 `pyproject.toml` 의 별도 extra 로 갈라 두었습니다.
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import imageio_ffmpeg

from src.config import UPLOADS_DIR


def ensure_dir() -> Path:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    return UPLOADS_DIR


def ensure_mp4(path: Path) -> Path:
    """mp4 가 아니면 H.264 mp4 로 변환합니다. 이미 mp4 면 그대로 둡니다."""
    path = Path(path)
    if path.suffix.lower() == ".mp4":
        return path

    out_path = path.with_suffix(".mp4")
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run(
        [
            ffmpeg_exe, "-y", "-i", str(path),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an",
            str(out_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    path.unlink(missing_ok=True)
    return out_path


def save_upload(content: bytes, filename: str) -> Path:
    """업로드 바이트를 저장하고 필요하면 mp4 로 맞춥니다.

    저장 이름은 uuid 입니다 — 사용자가 올린 이름을 파일 경로로 쓰면 경로 조작이 됩니다.
    원본 이름은 기록의 `source_file` 에 따로 남깁니다.
    """
    ensure_dir()
    suffix = Path(filename).suffix.lower() or ".mp4"
    dest = UPLOADS_DIR / f"{uuid.uuid4().hex[:8]}{suffix}"
    dest.write_bytes(content)
    return ensure_mp4(dest)


def download_url(url: str) -> tuple[Path, str | None]:
    """URL 의 영상을 받아옵니다. 반환: (경로, 제목).

    `yt-dlp` 가 병합에 쓰는 ffmpeg 는 시스템 PATH 대신 imageio-ffmpeg 내장 바이너리를
    넘겨 줍니다 (별도 설치 불필요). 일부 사이트의 봇 차단(HTTP 403)은 브라우저
    impersonation 으로 우회합니다 — `curl_cffi` 가 있어야 동작합니다.

    ⚠️ 이 함수는 선택 기능입니다. `yt-dlp` 가 없으면 ImportError 가 납니다 —
       부르는 쪽(`serve.py`)이 그것을 503 으로 옮깁니다.
    """
    import yt_dlp
    from yt_dlp.networking.impersonate import ImpersonateTarget

    ensure_dir()
    dest = UPLOADS_DIR / f"{uuid.uuid4().hex[:8]}.mp4"
    ydl_opts = {
        # keypoint 분석에 고해상도가 필요 없어 1080p 로 캡합니다 —
        # 불필요한 대용량 다운로드와 디코딩을 막습니다.
        "format": (
            "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/"
            "best[ext=mp4][height<=1080]/best"
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
