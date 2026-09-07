# -*- coding: utf-8 -*-
"""v4 pose 경로: 5fps 샘플 → ssdlite(direct) 박스 → RTMPose AP-10K ONNX → 후면 좌/우 x-순서 정렬.

walk_demo src/gait_demo/pose_backends.py 의 `_sample_frames / _fill_record / _ensure_rtmpose_onnx / _get_rtmpose /
run_rtmpose_ssd(direct 분기) / _lr_pairs / enforce_lr_by_x / run_pose` 와 src/gait_demo/keypoint_infer.py 의
`_base_frame_record` 를 로직 변경 없이 옮겼다. DLC 경로(dlc 백엔드)는 이식 대상이 아니라 뺐다.

record 스키마(프레임별): frame_idx, detected, bbox_frac, bbox_center, kps=[(name,x,y,conf),...], n_confident_kp,
  quality_flags, crop_assisted, crop_box, general_detector_conf, det_box, bbox_wh
"""
import time
import zipfile
from pathlib import Path
from urllib.request import urlretrieve

import cv2
import numpy as np

from . import ssdlite_detector
from .config import (AP10K_NAMES, BLUR_VAR_THRESH, FAR_BBOX_FRAC_THRESH, LR_FIX_MIN_HW, MODEL, MODEL_ID,
                     NIGHT_BRIGHTNESS_THRESH, RTMPOSE_INPUT_SIZE, RTMPOSE_ONNX, RTMPOSE_ONNX_URL, TARGET_FPS)


def _sample_frames(video_path):
    """production(extract_records)과 동일 규칙으로 5fps 서브샘플. returns ([(fidx, frame)], meta)"""
    cap = cv2.VideoCapture(str(video_path))
    native_fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    step = max(1, round(native_fps / TARGET_FPS))
    meta = {"native_fps": native_fps, "sample_fps": native_fps / step, "width": width, "height": height,
            "diag": float(np.hypot(width, height)), "step": step}
    frames, fidx, pos = [], 0, 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if pos % step == 0:
            frames.append((fidx, frame)); fidx += 1
        pos += 1
    cap.release()
    return frames, meta


def _base_frame_record(fidx, frame, width, height):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    brightness = float(gray.mean())
    blur_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    rec = {
        "frame_idx": fidx, "detected": False, "bbox_frac": None, "bbox_center": None,
        "kps": None, "n_confident_kp": 0, "quality_flags": [],
        "crop_assisted": False, "crop_box": None, "general_detector_conf": None,
    }
    if brightness < NIGHT_BRIGHTNESS_THRESH:
        rec["quality_flags"].append("night")
    if blur_var < BLUR_VAR_THRESH:
        rec["quality_flags"].append("blur")
    return rec


def _fill_record(rec, names, xy, conf, box, width, height, kp_conf):
    rec["detected"] = True
    rec["kps"] = [(names[i], float(xy[i][0]), float(xy[i][1]), float(conf[i])) for i in range(len(names))]
    rec["n_confident_kp"] = int(sum(1 for k in rec["kps"] if k[3] >= kp_conf))
    if box is not None:
        x1, y1, x2, y2 = box
        rec["det_box"] = [float(x1), float(y1), float(x2), float(y2)]
        rec["bbox_wh"] = (float(x2 - x1), float(y2 - y1))
        rec["bbox_frac"] = float(((x2 - x1) * (y2 - y1)) / (width * height)) if width and height else None
        rec["bbox_center"] = (float((x1 + x2) / 2), float((y1 + y2) / 2))
        if rec["bbox_frac"] is not None and rec["bbox_frac"] < FAR_BBOX_FRAC_THRESH:
            rec["quality_flags"].append("far")
    rec["general_detector_conf"] = None


# ---------------------------------------------------------------- RTMPose AP-10K
_rtm_cache = {}


def _ensure_rtmpose_onnx() -> Path:
    """없으면 OpenMMLab 공식 zip 을 받아 end2end.onnx 만 꺼낸다(52 MB)."""
    if RTMPOSE_ONNX.exists():
        return RTMPOSE_ONNX
    RTMPOSE_ONNX.parent.mkdir(parents=True, exist_ok=True)
    zpath = RTMPOSE_ONNX.parent / "rtmpose-m_ap10k.zip"
    print(f"[gait_v4] RTMPose ONNX 다운로드: {RTMPOSE_ONNX_URL}", flush=True)
    urlretrieve(RTMPOSE_ONNX_URL, zpath)
    with zipfile.ZipFile(zpath) as z:
        member = next(n for n in z.namelist() if n.endswith("end2end.onnx"))
        RTMPOSE_ONNX.write_bytes(z.read(member))
    zpath.unlink(missing_ok=True)
    return RTMPOSE_ONNX


def _get_rtmpose():
    if "model" not in _rtm_cache:
        from rtmlib import RTMPose
        _rtm_cache["model"] = RTMPose(str(_ensure_rtmpose_onnx()), model_input_size=RTMPOSE_INPUT_SIZE,
                                      backend="onnxruntime", device="cpu")
    return _rtm_cache["model"]


# ---------------------------------------------------------------- ssdlite(direct) 박스 → RTMPose
def run_rtmpose_ssd(video_path):
    m = MODEL; pose = _get_rtmpose()
    frames, meta = _sample_frames(video_path)
    t_det = time.time(); boxes, confs = {}, {}
    for fidx, frame in frames:
        hit = ssdlite_detector.detect(frame)
        if hit is not None:
            boxes[fidx], confs[fidx] = hit
    meta["superanimal"] = None
    meta["ssd_backend"] = "direct"
    meta["detect_sec"] = round(time.time() - t_det, 1)
    W, H = meta["width"], meta["height"]
    records = []
    t0 = time.time()
    for fidx, frame in frames:
        rec = _base_frame_record(fidx, frame, W, H)
        box = boxes.get(fidx)
        if box is not None:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)  # ONNX pipeline to_rgb: true (rtmlib 은 변환 안 함)
            kps, sc = pose(rgb, bboxes=[list(map(float, box))])
            _fill_record(rec, AP10K_NAMES, kps[0], sc[0], box, W, H, m["kp_conf"])
            rec["general_detector_conf"] = confs.get(fidx)
        records.append(rec)
    meta["bbox_source"] = "SuperAnimal ssdlite — torchvision 직접 호출 (DLC 없음)"
    meta["rtmpose_sec"] = round(time.time() - t0, 1)
    return records, meta


# ---------------------------------------------------------------- 좌/우 라벨 정렬 (README §11-3·§11-8)
def _lr_pairs(names):
    """관절 이름에서 (왼쪽, 오른쪽) 쌍을 찾는다. AP-10K 'L_Hip'↔'R_Hip'."""
    s = set(names); out = []
    for n in names:
        if "left" in n:
            p = n.replace("left", "right")
        elif n.startswith("L_"):
            p = "R_" + n[2:]
        else:
            continue
        if p in s:
            out.append((n, p))
    return out


def enforce_lr_by_x(records, kp_conf, min_hw=LR_FIX_MIN_HW, only_names=None):
    """후면 프레임(박스 h/w > min_hw)에서 좌/우 쌍의 x 순서를 강제한다(left.x > right.x 면 이름표 교환).
    record 의 kps 를 제자리에서 고치고 통계를 돌려준다. only_names: 이 이름들 사이의 쌍만."""
    pairs = None
    checked = swapped_frames = swapped_pairs = 0
    for rec in records:
        if not rec.get("detected") or not rec.get("kps") or not rec.get("bbox_wh"):
            continue
        w, h = rec["bbox_wh"]
        if w <= 0 or h / w <= min_hw:
            continue
        if pairs is None:
            pairs = _lr_pairs([k[0] for k in rec["kps"]])
            if only_names is not None:
                pairs = [(l, r) for l, r in pairs if l in only_names and r in only_names]
            if not pairs:
                return None
        checked += 1
        idx = {k[0]: i for i, k in enumerate(rec["kps"])}
        hit = 0
        for ln, rn in pairs:
            li, ri = idx.get(ln), idx.get(rn)
            if li is None or ri is None:
                continue
            L, R = rec["kps"][li], rec["kps"][ri]
            if L[3] >= kp_conf and R[3] >= kp_conf and L[1] > R[1]:
                rec["kps"][li] = (ln,) + tuple(R[1:])
                rec["kps"][ri] = (rn,) + tuple(L[1:])
                hit += 1
        if hit:
            swapped_frames += 1; swapped_pairs += hit
    return {"rule": "x-order (rear, bbox h/w>%.1f)" % min_hw, "frames_checked": checked,
            "frames_swapped": swapped_frames, "pairs_swapped": swapped_pairs}


# ---------------------------------------------------------------- 진입점
def run_pose(video_path):
    """(records, meta) — records 는 아직 gait 필터 전. meta 에 lr_fix, pose_model, elapsed_sec 포함."""
    t0 = time.time()
    records, meta = run_rtmpose_ssd(video_path)
    m = MODEL
    legs = {n for e in m["skeleton"] for n in e} | set(m["priority"])  # overlay 가 그리는 것과 같은 집합
    meta["lr_fix"] = enforce_lr_by_x(records, m["kp_conf"], only_names=legs)
    meta["pose_model"] = MODEL_ID
    meta["elapsed_sec"] = round(time.time() - t0, 1)
    return records, meta
