"""원거리 소형 피사체 보정 — 전용 모델이 실패했을 때만 도는 2단계 fallback.

전용 12kp 모델이 전체 프레임에서 개를 못 찾거나 관절을 충분히 못 잡을 때(원거리 촬영 등),
범용 COCO 사전학습 YOLO 로 개의 대략적 위치를 먼저 찾아 그 주변을 crop + 확대한 뒤
**전용 12kp 모델을 다시 한 번** 돌립니다.

**새 모델을 학습하지 않습니다.** 범용 모델(`yolov8n.pt`)은 ultralytics 가 공개 배포하는
COCO 가중치 그대로이고(다운로드만, 파인튜닝 없음), 전용 12kp 모델도 그대로입니다 —
crop 된 이미지에 적용할 뿐입니다.

파일럿 검증(원거리 영상 3개): usable 프레임 4→8 / 3→17 / 1→14 로 증가, bbox 흔들림
1~4%, 육안 확인 정상, feature 추출 호환 확인 후 정식 채택.

좌표계: crop + 업스케일된 이미지에서 나온 좌표는 `to_original()` 로 **항상 원본 프레임
픽셀 좌표로 되돌립니다.** feature 정규화는 이동 + 등방 스케일에 불변이라 되돌리든 말든
같지만, overlay 영상과 trajectory 는 원본 해상도 기준이라 되돌려야 맞습니다.
"""

from __future__ import annotations

import threading

import cv2
import numpy as np
from ultralytics import YOLO

from daengs_gait.config import (
    COCO_DOG_CLASS_ID,
    DETECTOR_WEIGHTS,
    GENERAL_CONF_THRESH,
    MARGIN_RATIO,
    TARGET_LONG_SIDE,
)

_general_model_cache: dict = {}
# keypoint_infer._MODEL_LOCK 과 같은 이유입니다 — threadpool 에서 동시에 들어오면
# 잠금 없이는 검출기를 두 벌 올립니다.
_general_model_lock = threading.Lock()


def get_general_model() -> YOLO:
    """범용 검출기를 프로세스당 한 번만 올립니다."""
    if "model" not in _general_model_cache:
        with _general_model_lock:
            if "model" not in _general_model_cache:
                if not DETECTOR_WEIGHTS.exists():
                    raise FileNotFoundError(
                        f"crop-assist 검출기 가중치를 찾을 수 없습니다: {DETECTOR_WEIGHTS}\n"
                        "GAIT_RELEASE_DIR 또는 GAIT_DETECTOR_WEIGHTS 를 확인하세요 "
                        "(가중치는 저장소에 없습니다 — README 참고)."
                    )
                _general_model_cache["model"] = YOLO(str(DETECTOR_WEIGHTS))
    return _general_model_cache["model"]


def release_general_model() -> None:
    """캐시된 모델을 놓습니다 (프로세스 종료 · 테스트용)."""
    _general_model_cache.clear()


def detect_dog_bbox(frame, general_model=None, conf_thresh: float = GENERAL_CONF_THRESH):
    """범용 YOLO 로 개 bbox 를 찾습니다.

    여러 마리이거나 오탐이 섞이면 confidence 가 가장 높은 것 **하나만** 씁니다.
    반환: (x1, y1, x2, y2, conf) 또는 못 찾으면 None.
    """
    model = general_model or get_general_model()
    res = model.predict(
        frame, conf=conf_thresh, iou=0.5, classes=[COCO_DOG_CLASS_ID], verbose=False
    )
    if not res or len(res[0].boxes) == 0:
        return None
    boxes = res[0].boxes.xyxy.cpu().numpy()
    confs = res[0].boxes.conf.cpu().numpy()
    best = int(np.argmax(confs))
    x1, y1, x2, y2 = boxes[best]
    return float(x1), float(y1), float(x2), float(y2), float(confs[best])


def crop_and_upscale(
    frame, box, margin_ratio: float = MARGIN_RATIO, target_long_side: int = TARGET_LONG_SIDE
):
    """bbox 주변을 여유 있게 잘라 확대합니다.

    반환: (업스케일 이미지, crop_box, to_original 함수) 또는 실패 시 (None, None, None).
    """
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    mx, my = bw * margin_ratio, bh * margin_ratio
    cx1 = max(0, int(x1 - mx))
    cy1 = max(0, int(y1 - my))
    cx2 = min(w, int(x2 + mx))
    cy2 = min(h, int(y2 + my))
    crop = frame[cy1:cy2, cx1:cx2]
    ch, cw = crop.shape[:2]
    if ch < 4 or cw < 4:
        return None, None, None

    scale = target_long_side / max(ch, cw)
    # 목적은 확대뿐입니다 — 이미 크면 줄이지 않습니다.
    scale = max(scale, 1.0)
    new_w, new_h = max(1, int(cw * scale)), max(1, int(ch * scale))
    interp = cv2.INTER_CUBIC if scale > 1.0 else cv2.INTER_AREA
    upscaled = cv2.resize(crop, (new_w, new_h), interpolation=interp)

    def to_original(x, y):
        return cx1 + x / scale, cy1 + y / scale

    return upscaled, (cx1, cy1, cx2, cy2), to_original


def try_crop_assisted_pose(frame, pose_model, keypoint_names, general_model=None):
    """전체 프레임에서 실패했을 때 시도하는 2단계 보정.

    반환: dict(kps=[(name, x, y, conf), ...] **원본 좌표**, bbox_frac, crop_box,
    general_detector_conf) 또는 None. 범용 검출기도 못 찾으면 억지로 처리하지 않고 None 입니다.
    """
    dog_box = detect_dog_bbox(frame, general_model)
    if dog_box is None:
        return None
    x1, y1, x2, y2, general_conf = dog_box

    upscaled, crop_box, to_original = crop_and_upscale(frame, (x1, y1, x2, y2))
    if upscaled is None:
        return None

    res = pose_model.predict(upscaled, conf=0.30, iou=0.55, verbose=False)
    if not res or res[0].keypoints is None or len(res[0].keypoints.xy) == 0:
        return None

    xy = res[0].keypoints.xy[0].cpu().numpy()
    conf = (
        res[0].keypoints.conf[0].cpu().numpy()
        if res[0].keypoints.conf is not None
        else np.ones(len(xy))
    )
    kps_original = []
    for i in range(len(keypoint_names)):
        ox, oy = to_original(float(xy[i, 0]), float(xy[i, 1]))
        kps_original.append((keypoint_names[i], ox, oy, float(conf[i])))

    box_pose = res[0].boxes.xyxy[0].cpu().numpy() if len(res[0].boxes) > 0 else None
    bbox_frac = None
    if box_pose is not None:
        # crop 안에서의 비율을 원본 프레임 기준 비율로 환산합니다.
        bw = (box_pose[2] - box_pose[0]) / upscaled.shape[1]
        bh = (box_pose[3] - box_pose[1]) / upscaled.shape[0]
        crop_w = crop_box[2] - crop_box[0]
        crop_h = crop_box[3] - crop_box[1]
        frame_h, frame_w = frame.shape[:2]
        bbox_frac = (bw * crop_w * bh * crop_h) / (frame_w * frame_h)

    return {
        "kps": kps_original,
        "bbox_frac": bbox_frac,
        "crop_box": crop_box,
        "general_detector_conf": general_conf,
    }
