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
from pathlib import Path

import cv2
import imageio_ffmpeg

from src.config import KP_MIN_CONF, SKELETON_CHAIN, TARGET_FPS

SKELETON_EDGES = list(zip(SKELETON_CHAIN[:-1], SKELETON_CHAIN[1:]))

# ⚠️ 추론의 TARGET_FPS 와 같아야 합니다. 다르면 그림과 원본 프레임이 어긋납니다.
OVERLAY_FPS = TARGET_FPS


def _draw_frame(frame, rec):
    if rec and rec.get("detected") and rec.get("kps"):
        pts = {name: (x, y, c) for name, x, y, c in rec["kps"]}
        for a, b in SKELETON_EDGES:
            if (
                a in pts
                and b in pts
                and pts[a][2] >= KP_MIN_CONF
                and pts[b][2] >= KP_MIN_CONF
            ):
                pa = (int(pts[a][0]), int(pts[a][1]))
                pb = (int(pts[b][0]), int(pts[b][1]))
                cv2.line(frame, pa, pb, (0, 200, 255), 2)
        for name, (x, y, c) in pts.items():
            if c >= KP_MIN_CONF:
                cv2.circle(frame, (int(x), int(y)), 4, (0, 0, 255), -1)
        # 이 프레임이 분석에 쓰였는지, 아니면 왜 빠졌는지를 그대로 적습니다.
        label = (
            "gait_usable" if rec.get("gait_usable") else f"exclude:{rec.get('exclude_reason')}"
        )
        color = (0, 200, 0) if rec.get("gait_usable") else (0, 0, 255)
        cv2.putText(frame, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
    return frame


def render_overlay_video(video_path, records: list, out_path) -> str:
    cap = cv2.VideoCapture(str(video_path))
    native_fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    # 추론과 같은 서브샘플 규칙이어야 프레임 인덱스가 맞습니다.
    step = max(1, round(native_fps / OVERLAY_FPS))

    by_fidx = {r["frame_idx"]: r for r in records}
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg_exe, "-y",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-pix_fmt", "bgr24", "-s", f"{width}x{height}", "-r", str(OVERLAY_FPS),
        "-i", "-",
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(out_path),
    ]
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    fidx = 0
    frame_pos = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_pos % step == 0:
            frame = _draw_frame(frame, by_fidx.get(fidx))
            proc.stdin.write(frame.tobytes())
            fidx += 1
        frame_pos += 1
    cap.release()
    proc.stdin.close()
    proc.wait()
    return str(out_path)
