# -*- coding: utf-8 -*-
"""skeleton overlay mp4 — walk_demo src/gait_demo/overlay.py 그대로(기본 스켈레톤만 v4 로).

5fps 서브샘플 프레임에만 그려 별도 mp4 로 저장하므로 원본보다 느리게 재생된다(UI 에 명시할 것).
인코딩은 cv2.VideoWriter 를 믿지 않고(플랫폼별 mp4v 폴백) imageio-ffmpeg 의 정적 ffmpeg 로 libx264 + faststart.
"""
import subprocess
from pathlib import Path

import cv2
import imageio_ffmpeg

from .config import AP10K_LINKS_HIND, KP_MIN_CONF, TARGET_FPS

SKELETON_EDGES = AP10K_LINKS_HIND
KP_CONF_THRESH = KP_MIN_CONF
OVERLAY_FPS = TARGET_FPS


def _side_color(name):
    """개 기준 왼쪽=시안, 오른쪽=주황, 정중선=흰색(BGR)."""
    n = name.lower()
    if name.startswith("L_") or "left" in n:
        return (200, 190, 40)
    if name.startswith("R_") or "right" in n:
        return (40, 140, 245)
    return None


def _draw_frame(frame, rec, edges=None, kp_conf=None, priority=None):
    edges = edges or SKELETON_EDGES
    kp_conf = KP_CONF_THRESH if kp_conf is None else kp_conf
    priority = set(priority or [])
    drawable = {n for e in edges for n in e} | priority  # 스켈레톤∪우선 관절만 그린다
    if rec and rec.get("detected") and rec.get("kps"):
        pts = {name: (x, y, c) for name, x, y, c in rec["kps"]}
        for a, b in edges:
            if a in pts and b in pts and pts[a][2] >= kp_conf and pts[b][2] >= kp_conf:
                pa = (int(pts[a][0]), int(pts[a][1]))
                pb = (int(pts[b][0]), int(pts[b][1]))
                ca, cb = _side_color(a), _side_color(b)
                col = ca if (ca is not None and ca == cb) else ((245, 245, 245) if (ca or cb) else (0, 200, 255))
                cv2.line(frame, pa, pb, col, 2)
        for name, (x, y, c) in pts.items():
            if name in drawable and c >= kp_conf:
                col = _side_color(name) or (0, 0, 255)
                r = 6 if name in priority else 4
                cv2.circle(frame, (int(x), int(y)), r, col, -1)
                if name in priority:
                    cv2.circle(frame, (int(x), int(y)), r, (20, 20, 20), 1)
        label = "gait_usable" if rec.get("gait_usable") else f"exclude:{rec.get('exclude_reason')}"
        color = (0, 200, 0) if rec.get("gait_usable") else (0, 0, 255)
        cv2.putText(frame, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
    return frame


def render_overlay_video(video_path, records, out_path, model_meta=None) -> str:
    edges = [tuple(e) for e in model_meta["skeleton"]] if model_meta else None
    kp_conf = model_meta["kp_conf"] if model_meta else None
    priority = model_meta["priority"] if model_meta else None
    cap = cv2.VideoCapture(str(video_path))
    native_fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    step = max(1, round(native_fps / OVERLAY_FPS))  # _sample_frames 와 동일 서브샘플 규칙

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
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    fidx = 0
    frame_pos = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_pos % step == 0:
            frame = _draw_frame(frame, by_fidx.get(fidx), edges=edges, kp_conf=kp_conf, priority=priority)
            proc.stdin.write(frame.tobytes())
            fidx += 1
        frame_pos += 1
    cap.release()
    proc.stdin.close()
    proc.wait()
    return str(out_path)
