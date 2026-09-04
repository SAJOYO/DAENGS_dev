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

import logging
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

import cv2
import imageio_ffmpeg

from daengs_gait.config import TARGET_FPS, UPLOADS_DIR

log = logging.getLogger(__name__)

# 프로브가 확인할 지점 수. 영상 전체 구간에 균등하게 흩습니다.
#
# 8 인 이유: 코덱 미지원(AV1 등)은 **첫 지점에서 바로** 드러나므로 개수가 많을 필요가
# 없고, 전체를 훑으면 분석을 두 번 하는 셈이라 비쌉니다(1080p 60초에 수십 초).
# 8 지점이면 1080p 60초에 0.3초 남짓입니다.
PROBE_POINTS = 8


class VideoDecodeError(RuntimeError):
    """OpenCV 로 읽을 수 없는 영상입니다. 변환까지 시도한 뒤에도 실패했을 때."""


@dataclass
class ProbeResult:
    ok: bool
    reason: str | None = None
    width: int = 0
    height: int = 0
    native_fps: float = 0.0
    frames_read: int = 0
    method: str = ""          # "seek" | "sequential"


def ensure_dir() -> Path:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    return UPLOADS_DIR


def probe_decodable(path: Path) -> ProbeResult:
    """**OpenCV 가 이 영상을 실제로 읽을 수 있는가.**

    확장자로 판단하지 않습니다 — 확장자는 코덱을 말해주지 않습니다. `.mov` 가 H.264 라
    잘 읽히는데 재인코딩당하고, `.mp4` 가 AV1 이라 **한 장도 못 읽는데** 그대로 통과하던
    것이 그래서였습니다 (2026-08-31 실측).

    분석(`keypoint_infer.extract_records`)이 실제로 쓰는 것만 봅니다:

      ① `isOpened()`
      ② `width` · `height` 가 둘 다 > 0
      ③ `native_fps` > 0 — `step = round(native_fps / TARGET_FPS)` 의 분자입니다
      ④ **프레임이 실제로 읽히는가** — 여러 지점에서

    ④ 를 첫 장으로만 보지 않는 이유: AV1 은 첫 장에서 바로 드러나지만 **중간부터
    깨지는 파일**은 첫 장이 성공합니다. 그래서 전체 구간에 `PROBE_POINTS` 개를 균등하게
    흩어 확인합니다.

    seek(`CAP_PROP_POS_FRAMES`)는 컨테이너·코덱에 따라 불안정합니다. **seek 가 어긋나면
    코덱 문제로 오판하게 되므로**, 한 지점이라도 실패하면 순차 읽기로 다시 확인해서
    "seek 이 안 되는 것"과 "디코딩이 안 되는 것"을 가릅니다.

    ⚠️ `TARGET_FPS` 를 **읽기만** 합니다 — 순차 폴백에서 분석과 같은 간격을 보려는
       것이고, 값을 바꾸지 않습니다.
    """
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            return ProbeResult(False, "컨테이너를 열 수 없습니다")

        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        native_fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

        # ⚠️ 이 셋은 **디코딩 없이 컨테이너 메타데이터만으로** 나옵니다. 그래서 여기까지
        #    통과해도 프레임이 읽힌다는 보장이 없습니다 — AV1 이 정확히 그랬습니다
        #    (video_meta 는 1080x1920/30fps 로 멀쩡한데 read() 가 첫 장부터 False).
        if width <= 0 or height <= 0:
            return ProbeResult(False, f"해상도를 읽을 수 없습니다 ({width}x{height})")
        if native_fps <= 0:
            return ProbeResult(False, f"fps 를 읽을 수 없습니다 ({native_fps})")

        base = ProbeResult(True, None, width, height, native_fps)

        if total > 1:
            r = _probe_by_seek(cap, total, base)
            if r.ok:
                return r
            # seek 이 흔들렸을 수 있습니다. 순차로 다시 봅니다.
            cap.release()
            cap = cv2.VideoCapture(str(path))
        return _probe_sequentially(cap, native_fps, base)
    finally:
        cap.release()


def _probe_by_seek(cap, total: int, base: ProbeResult) -> ProbeResult:
    """전체 구간의 `PROBE_POINTS` 지점을 균등하게 확인합니다 (0% ~ 끝 부근)."""
    # 마지막 프레임 자체는 컨테이너에 따라 읽기가 들쭉날쭉해서 한 칸 앞을 봅니다.
    last = max(0, total - 2)
    points = [round(last * i / (PROBE_POINTS - 1)) for i in range(PROBE_POINTS)]

    shapes = set()
    read = 0
    for pos in points:
        cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
        ok, frame = cap.read()
        if not ok or frame is None:
            return ProbeResult(False, f"프레임 {pos} 을 읽지 못했습니다", method="seek")
        shapes.add(frame.shape[:2])
        read += 1

    if len(shapes) > 1:
        # 프레임 크기가 도중에 바뀌면 overlay 인코딩이 stride 부터 어긋납니다.
        return ProbeResult(False, f"프레임 크기가 일정하지 않습니다 {shapes}", method="seek")

    return ProbeResult(True, None, base.width, base.height, base.native_fps, read, "seek")


def _probe_sequentially(cap, native_fps: float, base: ProbeResult) -> ProbeResult:
    """앞에서부터 **분석과 같은 간격**으로 `PROBE_POINTS` 장을 읽습니다.

    `total` 을 못 믿거나 seek 이 어긋난 경우의 폴백입니다.
    """
    step = max(1, round(native_fps / TARGET_FPS))
    shapes = set()
    read = 0
    pos = 0
    while read < PROBE_POINTS:
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        if pos % step == 0:
            shapes.add(frame.shape[:2])
            read += 1
        pos += 1

    if read == 0:
        return ProbeResult(False, "프레임을 하나도 읽지 못했습니다", method="sequential")
    if len(shapes) > 1:
        return ProbeResult(
            False, f"프레임 크기가 일정하지 않습니다 {shapes}", method="sequential"
        )
    # 영상이 프로브 개수보다 짧을 수 있습니다 — 읽힌 것이 있으면 통과입니다.
    return ProbeResult(True, None, base.width, base.height, base.native_fps, read, "sequential")


def ensure_mp4(path: Path) -> Path:
    """mp4 가 아니면 H.264 mp4 로 변환합니다. 이미 mp4 면 그대로 둡니다.

    ⚠️ **확장자만 봅니다.** 업로드 경로(`save_upload`)는 이제 이 함수를 쓰지 않고
       `probe_decodable` 로 판단합니다 — 확장자는 코덱을 말해주지 않아서, `.mov`(H.264)를
       불필요하게 재인코딩하고 `.mp4`(AV1)는 못 읽는 채로 통과시켰기 때문입니다.
       여기 남아 있는 이유는 `download_url` 이 아직 씁니다 (yt-dlp 산출물의 확장자가
       무엇일지 미리 알 수 없어 컨테이너를 맞춰 두는 용도).
    """
    path = Path(path)
    if path.suffix.lower() == ".mp4":
        return path

    return transcode_to_h264(path)


def transcode_to_h264(path: Path) -> Path:
    """H.264 mp4 로 재인코딩합니다. 원본 파일은 지웁니다.

    ⚠️ **손실 변환입니다.** 실측으로 111MB `.mov` 가 63MB 가 되면서 blur 플래그가
       170 → 200 으로 늘고 검출이 99 → 95 로 줄었습니다. 그래서 **읽을 수 있는 영상은
       변환하지 않습니다** — 읽지 못할 때의 마지막 수단입니다.
    """
    path = Path(path)
    out_path = path.with_suffix(".mp4")
    if out_path == path:
        out_path = path.with_name(path.stem + "_h264.mp4")
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    try:
        subprocess.run(
            [
                ffmpeg_exe, "-y", "-loglevel", "error", "-nostats", "-i", str(path),
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an",
                str(out_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        # ⚠️ **`CalledProcessError` 를 그대로 올리면 안 됩니다.** `service.py` 가 그것을
        #    `except Exception` 으로 받아 detail 에 문자열화하는데, 그 안에 **ffmpeg 명령줄
        #    전체와 서버 내부 경로**가 들어 있습니다 (실측 614자). 앱에 나가면 안 되는
        #    정보이고 사용자에게도 아무 도움이 안 됩니다.
        #
        #    사용자에게는 짧은 안내만 주고, 원인은 `from exc` 로 체인에 남겨 서버 로그에서
        #    추적할 수 있게 합니다.
        stderr = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
        log.error(
            "ffmpeg 변환 실패: %s (exit %s) stderr=%s",
            path.name, exc.returncode, stderr[-2000:],
        )
        raise VideoDecodeError(
            "이 영상 형식을 읽을 수 없습니다. "
            "지원되지 않는 코덱이거나 파일이 손상되었을 수 있습니다."
        ) from exc
    path.unlink(missing_ok=True)
    return out_path


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
    """
    ensure_dir()
    suffix = Path(filename).suffix.lower() or ".mp4"
    dest = UPLOADS_DIR / f"{uuid.uuid4().hex[:8]}{suffix}"
    dest.write_bytes(content)

    probe = probe_decodable(dest)
    if probe.ok:
        log.info(
            "[gait-intake] 원본 그대로 분석: %s  (%sx%s @%.3ffps, %s 로 %d장 확인)",
            dest, probe.width, probe.height, probe.native_fps, probe.method,
            probe.frames_read,
        )
        return dest

    log.warning(
        "[gait-intake] 원본을 읽지 못해 H.264 로 변환합니다: %s  사유=%s",
        dest, probe.reason,
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
        dest.name, converted, probe2.width, probe2.height, probe2.native_fps,
        probe2.method, probe2.frames_read,
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
