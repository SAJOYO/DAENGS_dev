# -*- coding: utf-8 -*-
"""gait_v4 상수 — walk_demo 의 값을 그대로 옮김 (2026-09-07). 값 하나라도 바꾸면 골든(tests/golden_v4_rear.json)과 어긋난다.

출처: src/e13_external_video_pilot.py, src/e14_gait_segment_filter.py, src/gait_demo/pose_backends.py,
      src/gait_demo/features.py, src/gait_demo/ssdlite_detector.py, src/gait_demo/quality_gate.py
"""
import os
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent
WEIGHTS_DIR = Path(os.environ.get("GAIT_V4_WEIGHTS", PKG_DIR.parent / "weights"))

# ---- 샘플링·관절 신뢰도 (e13)
TARGET_FPS = 5.0        # 원본 24~30fps 를 5fps 로 서브샘플
KP_MIN_CONF = 0.30      # 프레임 내 개별 keypoint 를 "검출됨" 으로 볼 conf 하한 (정규화·궤적·뭉침 검사 공통)

# ---- gait 필터 (e14)
MIN_CONFIDENT_KP = 6
MIN_BBOX_FRAC = 0.03
MAX_BBOX_FRAC = 0.65
STATIONARY_DISP_FRAC = 0.02
STATIONARY_SPEED_FRAC_PER_SEC = STATIONARY_DISP_FRAC / 0.2   # = 0.1/초
STATIONARY_MAX_GAP_SEC = 0.6
NIGHT_BRIGHTNESS_THRESH = 60.0
BLUR_VAR_THRESH = 80.0
FAR_BBOX_FRAC_THRESH = 0.08
MIN_KP_SPREAD_RATIO = 0.30
GAIT_FILTER_VERSION = "v5-stationary-speed-based-20260826"

# ---- feature (gait_demo/features.py)
FEATURE_VERSION = "v2-p90p10-added-20260904"
P90P10_MIN_FRAMES = 5
MIN_FRAMES_FOR_PERIODICITY = 8   # e3_trajectory_features

# ---- 품질 게이트 (gait_demo/quality_gate.py)
MIN_USABLE_FRAMES = 4

# ---- 좌/우 정렬 (pose_backends)
LR_FIX_MIN_HW = 1.0   # 박스 높이/폭 이 이보다 커야 x-순서 정렬 적용 (후면 자세)

# ---- ssdlite 검출기 (ssdlite_detector)
SSDLITE_WEIGHTS = WEIGHTS_DIR / "ssdlite.pt"
SSDLITE_SCORE_THRESH = 0.01

# ---- RTMPose AP-10K (ONNX)
RTMPOSE_ONNX = WEIGHTS_DIR / "rtmpose-m_ap10k" / "end2end.onnx"
RTMPOSE_ONNX_URL = ("https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/"
                    "rtmpose-m_simcc-ap10k_pt-aic-coco_210e-256x256-7a041aa1_20230206.zip")
RTMPOSE_INPUT_SIZE = (256, 256)

AP10K_NAMES = ["L_Eye", "R_Eye", "Nose", "Neck", "Root of tail",
               "L_Shoulder", "L_Elbow", "L_F_Paw", "R_Shoulder", "R_Elbow", "R_F_Paw",
               "L_Hip", "L_Knee", "L_B_Paw", "R_Hip", "R_Knee", "R_B_Paw"]
# 등선(Neck–Root of tail) + 꼬리뿌리→엉덩이 + 뒷다리 체인. 머리·앞다리 제외 (README §11-8)
AP10K_LINKS_HIND = [("Neck", "Root of tail"),
                    ("Root of tail", "L_Hip"), ("L_Hip", "L_Knee"), ("L_Knee", "L_B_Paw"),
                    ("Root of tail", "R_Hip"), ("R_Hip", "R_Knee"), ("R_Knee", "R_B_Paw")]

MODEL_ID = "rtmpose_ap10k_ssd"
MODEL = {
    "label": "RTMPose AP-10K + ssdlite 박스 (17kp)",
    "short": "RTM+ssd",
    "joints": AP10K_NAMES,
    "priority": ["L_Hip", "L_Knee", "L_B_Paw", "R_Hip", "R_Knee", "R_B_Paw"],
    "skeleton": AP10K_LINKS_HIND,
    "kp_conf": 0.30, "min_confident_kp": 6, "spread_check": False, "bbox_frac_range": (0.0, 0.65),
    "desc": "RTMPose 관절망(발 정밀도 최고)에 SuperAnimal ssdlite 검출기를 붙인 조합 — 후면 검출 100%. README §11-10.",
}


def model_meta() -> dict:
    m = MODEL
    return {"id": MODEL_ID, "label": m["label"], "short": m["short"], "joints": list(m["joints"]),
            "priority": list(m["priority"]), "skeleton": [list(e) for e in m["skeleton"]],
            "kp_conf": m["kp_conf"], "desc": m["desc"]}
