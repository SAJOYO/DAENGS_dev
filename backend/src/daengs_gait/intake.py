"""영상 입력 판정 — **읽을 수 있으면 원본 그대로, 못 읽을 때만 변환, 그래도 못 읽으면 실패.**

워커(`daengs_backend.services.gait._analyze_from_storage`)가 스토리지에서 받은 파일을 엔진에
넘기기 **직전**에 부릅니다. 엔진(legacy · v4)은 판정을 모르고 확정된 경로를 받기만 합니다
(D-063 3단계).

원칙은 8e4a255(2026-09-01)에서 실측으로 정한 것입니다 — 같은 `IMG_8631.mov` 를 원본으로 읽으면
walk_demo 와 298 프레임 전 필드가 일치하는데, `.mp4` 로 재인코딩하면 blur 170→200 · usable 3→5
로 갈렸습니다. **확장자로 판단하지 않습니다**: `.mov`(H.264)는 잘 읽히고 `.mp4`(AV1)는 한 장도
못 읽는 일이 실제로 있었습니다.

⚠️ 이 모듈은 import 가 가벼워야 합니다. `cv2` · `imageio_ffmpeg` 는 **실제로 판정·변환하는
   함수 안에서만** import 합니다 — 정책 테스트가 cv2 없는 CI 에서 돌고, backend 웹 프로세스가
   (함수 안에서) import 해도 무거운 것이 딸려 오지 않게.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from daengs_gait.config import TARGET_FPS

log = logging.getLogger(__name__)

# 프로브가 확인할 지점 수. 영상 전체 구간에 균등하게 흩습니다.
#
# 8 인 이유: 코덱 미지원(AV1 등)은 **첫 지점에서 바로** 드러나므로 개수가 많을 필요가
# 없고, 전체를 훑으면 분석을 두 번 하는 셈이라 비쌉니다(1080p 60초에 수십 초).
# 8 지점이면 1080p 60초에 0.3초 남짓입니다.
PROBE_POINTS = 8


class VideoDecodeError(RuntimeError):
    """OpenCV 로 읽을 수 없는 영상입니다. 변환까지 시도한 뒤에도 실패했을 때.

    문장은 사용자에게 그대로 나갈 수 있습니다(`failure_reason`) — 경로·명령줄을 넣지 않습니다.
    """


@dataclass
class ProbeResult:
    ok: bool
    reason: str | None = None
    width: int = 0
    height: int = 0
    native_fps: float = 0.0
    frames_read: int = 0
    method: str = ""  # "seek" | "sequential"


def probe_decodable(path: Path) -> ProbeResult:
    """**OpenCV 가 이 영상을 실제로 읽을 수 있는가.**

    확장자로 판단하지 않습니다 — 확장자는 코덱을 말해주지 않습니다. `.mov` 가 H.264 라
    잘 읽히는데 재인코딩당하고, `.mp4` 가 AV1 이라 **한 장도 못 읽는데** 그대로 통과하던
    것이 그래서였습니다 (2026-08-31 실측).

    분석(`keypoint_infer.extract_records` · v4 `pose._sample_frames`)이 실제로 쓰는 것만 봅니다:

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
    import cv2  # 지연 — 이 모듈 import 를 가볍게 (CI 에는 cv2 가 없습니다)

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
    import cv2

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
        return ProbeResult(False, f"프레임 크기가 일정하지 않습니다 {shapes}", method="sequential")
    # 영상이 프로브 개수보다 짧을 수 있습니다 — 읽힌 것이 있으면 통과입니다.
    return ProbeResult(True, None, base.width, base.height, base.native_fps, read, "sequential")


def transcode_to_h264(path: Path) -> Path:
    """H.264 mp4 로 재인코딩합니다. 원본 파일은 지웁니다.

    ⚠️ **손실 변환입니다.** 실측으로 111MB `.mov` 가 63MB 가 되면서 blur 플래그가
       170 → 200 으로 늘고 검출이 99 → 95 로 줄었습니다. 그래서 **읽을 수 있는 영상은
       변환하지 않습니다** — 읽지 못할 때의 마지막 수단입니다.

    입력 이름에 확장자가 없어도(워커의 `input.bin`) 됩니다 — ffmpeg 는 내용으로 컨테이너를
    판별합니다. 출력은 `<이름>.mp4` 입니다.
    """
    import imageio_ffmpeg  # 지연 — 이 모듈 import 를 가볍게

    path = Path(path)
    out_path = path.with_suffix(".mp4")
    if out_path == path:
        out_path = path.with_name(path.stem + "_h264.mp4")
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    try:
        subprocess.run(
            [
                ffmpeg_exe,
                "-y",
                "-loglevel",
                "error",
                "-nostats",
                "-i",
                str(path),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-an",
                str(out_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        # ⚠️ **`CalledProcessError` 를 그대로 올리면 안 됩니다.** 그 메시지에는 **ffmpeg 명령줄
        #    전체와 서버 내부 경로**가 들어 있습니다 (실측 614자). `failure_reason` 으로 앱에
        #    나갈 수 있는 자리라 사용자에게는 짧은 안내만 주고, 원인은 `from exc` 로 체인에
        #    남겨 서버 로그에서 추적합니다.
        stderr = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
        log.error(
            "ffmpeg 변환 실패: %s (exit %s) stderr=%s",
            path.name,
            exc.returncode,
            stderr[-2000:],
        )
        raise VideoDecodeError(
            "이 영상 형식을 읽을 수 없습니다. "
            "지원되지 않는 코덱이거나 파일이 손상되었을 수 있습니다."
        ) from exc
    path.unlink(missing_ok=True)
    return out_path


def prepare_for_analysis(path: Path) -> Path:
    """엔진에 넘길 파일을 확정합니다 — **원본 보존이 기본**입니다.

    - 읽을 수 있으면 `path` 를 **그대로** 돌려줍니다 (파일 생성·변환 없음).
    - 못 읽으면 `transcode_to_h264` 로 변환하고 **변환본을 다시 확인**한 뒤 그 경로를 돌려줍니다.
      변환은 원본을 지우므로 같은 디렉터리 안에서만 움직입니다.
    - 변환 후에도 못 읽거나 ffmpeg 가 실패하면 `VideoDecodeError` 입니다 — 조용히 넘기면 분석이
      `sampled: 0` 으로 끝나고 사용자는 "다시 촬영해 주세요" 라는 엉뚱한 안내를 받습니다.

    옛 HTTP 서비스의 `video_intake.save_upload` 에서 **판정 부분만** 떼어 낸 것입니다 —
    저장·이름 짓기는 호출자(워커의 임시 디렉터리) 몫입니다.
    """
    path = Path(path)
    probe = probe_decodable(path)
    if probe.ok:
        log.info(
            "[gait-intake] 원본 그대로 분석: %s  (%sx%s @%.3ffps, %s 로 %d장 확인)",
            path.name,
            probe.width,
            probe.height,
            probe.native_fps,
            probe.method,
            probe.frames_read,
        )
        return path

    log.warning(
        "[gait-intake] 원본을 읽지 못해 H.264 로 변환합니다: %s  사유=%s",
        path.name,
        probe.reason,
    )
    converted = transcode_to_h264(path)

    # ⚠️ **변환했다고 읽힌다는 보장이 없습니다.** 반드시 다시 확인합니다.
    probe2 = probe_decodable(converted)
    if not probe2.ok:
        raise VideoDecodeError(
            f"이 영상 형식을 읽을 수 없습니다 (변환 후에도 실패: {probe2.reason}). "
            "H.264 로 촬영·저장된 mp4 로 다시 올려 주세요."
        )

    log.info(
        "[gait-intake] 재인코딩 후 분석: %s → %s  (%sx%s @%.3ffps, %s 로 %d장 확인)",
        path.name,
        converted.name,
        probe2.width,
        probe2.height,
        probe2.native_fps,
        probe2.method,
        probe2.frames_read,
    )
    return converted
