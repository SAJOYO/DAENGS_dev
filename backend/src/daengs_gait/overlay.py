"""skeleton overlay 영상 — 사용자가 분석 결과를 눈으로 확인하는 산출물.

원본 영상 위에 keypoint 점과 관절 연결선을 그립니다. 추론이 이미 서브샘플한 프레임
(기본 5fps)에만 그리므로 **출력 영상은 원본보다 느리게 재생됩니다** — 화면에 그 사실을
적어 두어야 사용자가 "우리 개가 느려졌다"로 오해하지 않습니다.

인코딩 (실측으로 확정한 것, 바꾸지 마세요):
  `cv2.VideoWriter` 의 `mp4v` 는 MPEG-4 Part 2 라 브라우저 `<video>` 가 재생하지 못합니다.
  `avc1`(H.264)로 바꿔 봤지만 OpenH264 DLL 이 없는 환경에서 cv2 내부 인코더가 실행마다
  다르게 폴백해 조용히 mp4v 로 되돌아갔습니다 — `isOpened()` 가 True 를 줘도 실제로는
  실패라 신뢰할 수 없었습니다. 그래서 **cv2 로는 그림만 그리고 인코딩은 imageio-ffmpeg 의
  정적 바이너리**(libx264 내장)에 raw 프레임을 파이프로 흘려보내 처리합니다.
  `-movflags +faststart` 는 moov atom 을 앞에 둬서 다운로드가 끝나기 전에 재생이
  시작되게 합니다.
"""

from __future__ import annotations

import subprocess
from itertools import pairwise
from pathlib import Path

import cv2
import imageio_ffmpeg

from daengs_gait.config import KP_MIN_CONF, SKELETON_CHAIN, TARGET_FPS

SKELETON_EDGES = list(pairwise(SKELETON_CHAIN))

# ⚠️ 추론의 TARGET_FPS 와 같아야 합니다. 다르면 그림과 원본 프레임이 어긋납니다.
OVERLAY_FPS = TARGET_FPS


def _side_color(name: str):
    """개 기준 왼쪽=시안, 오른쪽=주황, 정중선/구분 없음=None(BGR). v4(`L_`/`R_`/left/right
    접두 관절)에서만 값을 내고, legacy 12kp 이름은 전부 None 이라 이 함수가 있어도
    legacy 출력은 바뀌지 않습니다."""
    n = name.lower()
    if name.startswith("L_") or "left" in n:
        return (200, 190, 40)
    if name.startswith("R_") or "right" in n:
        return (40, 140, 245)
    return None


def _draw_frame(frame, rec, *, edges=None, kp_conf=None, priority=None):
    """엣지·신뢰도 임계값·우선 관절은 **엔진마다 다른 부분**입니다(D-063 5B) — 옛
    `gait_v4/overlay.py` 의 `_draw_frame` 을 인자로 흡수한 것입니다. 인자를 안 주면
    (legacy 기본) 출력이 한 글자도 바뀌지 않습니다: `edges` 기본은 `SKELETON_EDGES`
    (legacy 12 관절 전부를 잇는 체인이라 `drawable` 이 항상 전체 집합과 같고),
    `priority` 기본은 빈 집합이라 반지름·외곽선이 전부 그대로이며, legacy 관절
    이름은 `_side_color` 가 전부 None 을 내 색도 그대로입니다."""
    edges = SKELETON_EDGES if edges is None else edges
    kp_conf = KP_MIN_CONF if kp_conf is None else kp_conf
    priority = set(priority or [])
    # 스켈레톤∪우선 관절만 그립니다 — legacy 는 체인이 12 관절을 전부 지나 이 필터가
    # 있으나 없으나 같습니다. v4 는 눈·코 등 그리지 않는 관절을 걸러내는 용도입니다.
    drawable = {n for e in edges for n in e} | priority
    if rec and rec.get("detected") and rec.get("kps"):
        pts = {name: (x, y, c) for name, x, y, c in rec["kps"]}
        for a, b in edges:
            if a in pts and b in pts and pts[a][2] >= kp_conf and pts[b][2] >= kp_conf:
                pa = (int(pts[a][0]), int(pts[a][1]))
                pb = (int(pts[b][0]), int(pts[b][1]))
                ca, cb = _side_color(a), _side_color(b)
                if ca is None and cb is None:
                    col = (0, 200, 255)
                elif ca is not None and ca == cb:
                    col = ca
                else:
                    col = (245, 245, 245)
                cv2.line(frame, pa, pb, col, 2)
        for name, (x, y, c) in pts.items():
            if name in drawable and c >= kp_conf:
                col = _side_color(name) or (0, 0, 255)
                r = 6 if name in priority else 4
                cv2.circle(frame, (int(x), int(y)), r, col, -1)
                if name in priority:
                    cv2.circle(frame, (int(x), int(y)), r, (20, 20, 20), 1)
        # 이 프레임이 분석에 쓰였는지, 아니면 왜 빠졌는지를 그대로 적습니다.
        label = (
            "gait_usable" if rec.get("gait_usable") else f"exclude:{rec.get('exclude_reason')}"
        )
        color = (0, 200, 0) if rec.get("gait_usable") else (0, 0, 255)
        cv2.putText(frame, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
    return frame


class OverlayEncodeError(RuntimeError):
    """overlay 인코딩이 실패했습니다 — 산출물을 믿으면 안 되는 상태."""


def render_overlay_video(video_path, records: list, out_path, model_meta: dict | None = None) -> str:
    """`model_meta` 를 주면(v4) 그 엔진의 스켈레톤·신뢰도·우선 관절로 그립니다 — 없으면
    (legacy, 기본) `_draw_frame` 의 기본 인자를 그대로 씁니다(D-063 5B, 5C 의 같은 패턴)."""
    edges = [tuple(e) for e in model_meta["skeleton"]] if model_meta else None
    kp_conf = model_meta["kp_conf"] if model_meta else None
    priority = model_meta["priority"] if model_meta else None
    cap = cv2.VideoCapture(str(video_path))
    native_fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    # 추론과 같은 서브샘플 규칙이어야 프레임 인덱스가 맞습니다.
    step = max(1, round(native_fps / OVERLAY_FPS))

    # ⚠️ **크기는 `CAP_PROP_FRAME_WIDTH/HEIGHT` 가 아니라 실제로 디코드된 프레임에서
    #    가져옵니다.** 회전 메타데이터가 붙은 영상(안드로이드 세로 촬영 — 이 서비스가
    #    상정하는 입력입니다)은 OpenCV 의 FFMPEG 백엔드가 `read()` 에서 자동으로 세워
    #    주는데, `CAP_PROP` 은 회전 전 컨테이너 값을 그대로 냅니다. 그 값을 ffmpeg 의
    #    `-s` 에 넣으면 파이프로 흘려보내는 `frame.tobytes()` 와 stride 가 어긋나
    #    **overlay 가 비스듬히 찢어지는데 예외는 하나도 안 납니다.**
    ret, first_frame = cap.read()
    if not ret:
        cap.release()
        raise OverlayEncodeError(f"영상에서 프레임을 하나도 읽지 못했습니다: {video_path}")
    height, width = first_frame.shape[:2]

    by_fidx = {r["frame_idx"]: r for r in records}
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg_exe, "-y",
        # stderr 를 PIPE 로 받으므로 출력량을 줄입니다. 진행률(-stats)까지 흘리면
        # 파이프 버퍼가 차서 교착이 날 수 있습니다.
        "-loglevel", "error", "-nostats",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-pix_fmt", "bgr24", "-s", f"{width}x{height}", "-r", str(OVERLAY_FPS),
        "-i", "-",
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(out_path),
    ]
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )

    expected_nbytes = width * height * 3
    frame = first_frame
    fidx = 0
    frame_pos = 0
    broken_pipe = False
    while True:
        if frame_pos % step == 0:
            frame = _draw_frame(frame, by_fidx.get(fidx), edges=edges, kp_conf=kp_conf, priority=priority)
            buf = frame.tobytes()
            if len(buf) != expected_nbytes:
                # 프레임 크기가 도중에 달라지면 이후 전부가 어긋납니다. 조용히 찢어진
                # 영상을 내놓느니 여기서 멈춥니다.
                proc.stdin.close()
                proc.stderr.close()
                proc.kill()
                cap.release()
                raise OverlayEncodeError(
                    f"프레임 크기가 도중에 바뀌었습니다 "
                    f"(기대 {width}x{height}, 프레임 {frame_pos}: "
                    f"{frame.shape[1]}x{frame.shape[0]})."
                )
            try:
                proc.stdin.write(buf)
            except OSError:
                # ffmpeg 가 먼저 죽은 경우입니다. 아래에서 종료 코드와 stderr 로 봅니다.
                # ⚠️ `BrokenPipeError` 만 잡으면 안 됩니다 — 윈도우에서는 같은 상황이
                #    `OSError: [Errno 22] Invalid argument` 로 옵니다.
                broken_pipe = True
                break
            fidx += 1
        frame_pos += 1
        ret, frame = cap.read()
        if not ret:
            break
    cap.release()

    try:
        proc.stdin.close()
    except OSError:
        # 위와 같습니다 — 죽은 파이프를 닫을 때 윈도우는 Errno 22 를 냅니다.
        pass
    stderr = proc.stderr.read().decode("utf-8", errors="replace").strip()
    proc.stderr.close()
    returncode = proc.wait()

    # ⚠️ **종료 코드를 반드시 봅니다.** 예전에는 이것을 버리고 경로를 그대로 반환해서,
    #    인코딩이 실패해도 기록이 `overlay_video` 를 있다고 광고했습니다. 사용자는
    #    404 를 받는데 서버에는 이유가 아무 데도 안 남았습니다.
    if returncode != 0 or broken_pipe:
        raise OverlayEncodeError(
            f"ffmpeg 인코딩 실패 (exit {returncode})"
            + (f": {stderr[-2000:]}" if stderr else "")
        )
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise OverlayEncodeError(f"overlay 파일이 만들어지지 않았습니다: {out_path}")
    return str(out_path)
