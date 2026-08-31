"""영상 → 프레임별 12-keypoint 추론 결과.

전용 12kp 모델(`best.pt`)이 1차이고, 그것이 개를 못 찾거나 관절을 충분히 못 잡은
프레임에 한해서만 crop-assist 2차 보정을 시도합니다 (`crop_assist.py`).

원본: walk_demo `gait_demo/keypoint_infer.py`. 상수 import 를 `config.py` 로 바꾼 것
외에는 추론 로직이 같습니다.
"""

from __future__ import annotations

import threading

import cv2
import numpy as np
from ultralytics import YOLO

from daengs_gait.config import (
    BLUR_VAR_THRESH,
    CONF_THRESH,
    FAR_BBOX_FRAC_THRESH,
    KEYPOINT_NAMES,
    KP_MIN_CONF,
    MIN_CONFIDENT_KP,
    NIGHT_BRIGHTNESS_THRESH,
    POSE_WEIGHTS,
    TARGET_FPS,
)
from daengs_gait.crop_assist import get_general_model, try_crop_assisted_pose
from daengs_gait.gait_filter import apply_gait_filter

_MODEL_CACHE: dict = {}
# ⚠️ serve.py 가 `process_video` 를 threadpool 로 돌리므로 **분석 요청이 동시에 들어옵니다.**
#    잠금 없이 "없으면 올린다" 를 하면 두 스레드가 동시에 통과해 torch 모델을 두 벌
#    올립니다 — 결과가 오염되지는 않지만 메모리가 두 배가 됩니다.
_MODEL_LOCK = threading.Lock()


def get_model() -> YOLO:
    """전용 12kp pose 모델을 프로세스당 한 번만 올립니다."""
    if "model" not in _MODEL_CACHE:
        with _MODEL_LOCK:
            # 잠금을 잡는 동안 다른 스레드가 이미 올렸을 수 있어 한 번 더 봅니다.
            if "model" not in _MODEL_CACHE:
                if not POSE_WEIGHTS.exists():
                    raise FileNotFoundError(
                        f"12kp pose 가중치를 찾을 수 없습니다: {POSE_WEIGHTS}\n"
                        "GAIT_RELEASE_DIR 또는 GAIT_POSE_WEIGHTS 를 확인하세요 "
                        "(가중치는 저장소에 없습니다 — README 참고)."
                    )
                _MODEL_CACHE["model"] = YOLO(str(POSE_WEIGHTS))
    return _MODEL_CACHE["model"]


def release_model() -> None:
    """캐시된 모델을 놓습니다 (프로세스 종료 · 테스트용)."""
    _MODEL_CACHE.clear()


def _base_frame_record(fidx: int, frame) -> dict:
    """한 프레임의 기본 레코드 + 품질 플래그(제외 사유가 아니라 기록만)."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    brightness = float(gray.mean())
    blur_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    rec = {
        "frame_idx": fidx,
        "detected": False,
        "bbox_frac": None,
        "bbox_center": None,
        "kps": None,
        "n_confident_kp": 0,
        "quality_flags": [],
        "crop_assisted": False,
        "crop_box": None,
        "general_detector_conf": None,
    }
    if brightness < NIGHT_BRIGHTNESS_THRESH:
        rec["quality_flags"].append("night")
    if blur_var < BLUR_VAR_THRESH:
        rec["quality_flags"].append("blur")
    return rec


def _fill_from_pose_result(rec: dict, res, width: int, height: int) -> None:
    xy = res[0].keypoints.xy[0].cpu().numpy()
    conf = (
        res[0].keypoints.conf[0].cpu().numpy()
        if res[0].keypoints.conf is not None
        else np.ones(len(xy))
    )
    box = res[0].boxes.xyxy[0].cpu().numpy() if len(res[0].boxes) > 0 else None
    rec["detected"] = True
    rec["kps"] = [
        (KEYPOINT_NAMES[i], float(xy[i, 0]), float(xy[i, 1]), float(conf[i]))
        for i in range(len(KEYPOINT_NAMES))
    ]
    rec["n_confident_kp"] = int(sum(1 for _, _, _, c in rec["kps"] if c >= KP_MIN_CONF))
    if box is not None:
        bw, bh = box[2] - box[0], box[3] - box[1]
        rec["bbox_frac"] = float((bw * bh) / (width * height)) if width and height else None
        rec["bbox_center"] = (float((box[0] + box[2]) / 2), float((box[1] + box[3]) / 2))
        if rec["bbox_frac"] is not None and rec["bbox_frac"] < FAR_BBOX_FRAC_THRESH:
            rec["quality_flags"].append("far")


def extract_records(video_path, pose_model, general_model=None, use_crop_assist: bool = True):
    """영상 → (프레임별 레코드 목록, 영상 메타데이터).

    원본 fps 를 그대로 돌지 않고 `TARGET_FPS` 로 서브샘플합니다.
    """
    cap = cv2.VideoCapture(str(video_path))
    native_fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    diag = float(np.hypot(width, height))
    step = max(1, round(native_fps / TARGET_FPS))

    records = []
    fidx = 0
    frame_pos = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_pos % step == 0:
            rec = _base_frame_record(fidx, frame)
            res = pose_model.predict(frame, conf=CONF_THRESH, iou=0.55, verbose=False)
            if res and res[0].keypoints is not None and len(res[0].keypoints.xy) > 0:
                _fill_from_pose_result(rec, res, width, height)

            needs_rescue = use_crop_assist and (
                (not rec["detected"]) or (rec["n_confident_kp"] < MIN_CONFIDENT_KP)
            )
            if needs_rescue:
                crop_result = try_crop_assisted_pose(
                    frame, pose_model, KEYPOINT_NAMES, general_model=general_model
                )
                if crop_result is not None:
                    n_confident_crop = sum(
                        1 for _, _, _, c in crop_result["kps"] if c >= KP_MIN_CONF
                    )
                    # 2차가 1차보다 나을 때만 갈아 끼웁니다 — 보정이 늘 이기는 게 아닙니다.
                    if n_confident_crop > rec["n_confident_kp"]:
                        rec["detected"] = True
                        rec["kps"] = crop_result["kps"]
                        rec["n_confident_kp"] = n_confident_crop
                        rec["bbox_frac"] = crop_result["bbox_frac"]
                        xs = [p[1] for p in crop_result["kps"]]
                        ys = [p[2] for p in crop_result["kps"]]
                        rec["bbox_center"] = (float(np.mean(xs)), float(np.mean(ys)))
                        rec["crop_assisted"] = True
                        rec["crop_box"] = crop_result["crop_box"]
                        rec["general_detector_conf"] = crop_result["general_detector_conf"]
            records.append(rec)
            fidx += 1
        frame_pos += 1
    cap.release()

    # ⚠️ 실제로 샘플링된 fps 를 그대로 넘깁니다. `TARGET_FPS` 와 근사하지만 step 이
    #    정수라 반올림 오차가 있고, 그 차이가 정지 판정(시간 기반 임계값)에 들어갑니다.
    sample_fps = native_fps / step
    return records, {
        "native_fps": native_fps,
        "sample_fps": sample_fps,
        "width": width,
        "height": height,
        "diag": diag,
    }


def run_keypoint_inference(video_path):
    """영상 → (보행 필터까지 적용된 프레임 레코드, 영상 메타데이터)."""
    model = get_model()
    general_model = get_general_model()
    records, meta = extract_records(str(video_path), model, general_model=general_model)
    records = apply_gait_filter(
        records,
        meta["diag"],
        min_confident_kp=MIN_CONFIDENT_KP,
        sample_fps=meta["sample_fps"],
    )
    return records, meta
