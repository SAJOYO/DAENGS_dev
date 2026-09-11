"""v4 모델·런타임 상수 — SuperAnimal ssdlite + RTMPose AP-10K 조합에만 쓰입니다.

`daengs_gait/config.py` 의 도메인 임계값(legacy·v4 공통, 5C)과는 **다른 파일입니다** —
여기 상수는 이 추론 조합 하나의 것이고, 값이 바뀌어도 도메인 판정 기준에는 영향이
없습니다(D-063 5B 재점검 #4). `backend/gait_v4/gait_v4/config.py` 에서 값 그대로
옮겼습니다 — 하나라도 바꾸면 골든(`backend/tests/test_gait_v4_golden.py`)과 어긋납니다.

⚠️ **가벼워야 합니다.** import 는 `daengs_gait.config`(os·pathlib 뿐) 하나입니다 —
   `daengs_gait.inference` 패키지는 자식 프로세스(서브프로세스 브리지)에서만 무거운
   라이브러리를 끌어옵니다(`tests/test_gait_v4_compare_import_boundary.py`).
"""

from __future__ import annotations

# `daengs_gait/config.py` 의 RELEASE_DIR(`GAIT_RELEASE_DIR`)과 legacy 가 같은 폴더를
# 씁니다 — 서버는 이미 그렇게 마운트돼 있습니다(D-063 5B). 파일 이름이 달라
# (`ssdlite.pt`·`rtmpose-m_ap10k/`) legacy 가중치(`best.pt`·`yolov8n.pt`)와 안 겹칩니다.
from daengs_gait.config import RELEASE_DIR

WEIGHTS_DIR = RELEASE_DIR

# ---- 좌/우 정렬 (pose_backends)
LR_FIX_MIN_HW = 1.0  # 박스 높이/폭 이 이보다 커야 x-순서 정렬 적용 (후면 자세)

# ---- ssdlite 검출기
SSDLITE_WEIGHTS = WEIGHTS_DIR / "ssdlite.pt"
SSDLITE_SCORE_THRESH = 0.01

# ---- RTMPose AP-10K (ONNX)
RTMPOSE_ONNX = WEIGHTS_DIR / "rtmpose-m_ap10k" / "end2end.onnx"
RTMPOSE_ONNX_URL = (
    "https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/"
    "rtmpose-m_simcc-ap10k_pt-aic-coco_210e-256x256-7a041aa1_20230206.zip"
)
RTMPOSE_INPUT_SIZE = (256, 256)

AP10K_NAMES = [
    "L_Eye", "R_Eye", "Nose", "Neck", "Root of tail",
    "L_Shoulder", "L_Elbow", "L_F_Paw", "R_Shoulder", "R_Elbow", "R_F_Paw",
    "L_Hip", "L_Knee", "L_B_Paw", "R_Hip", "R_Knee", "R_B_Paw",
]
# 등선(Neck–Root of tail) + 꼬리뿌리→엉덩이 + 뒷다리 체인. 머리·앞다리 제외.
AP10K_LINKS_HIND = [
    ("Neck", "Root of tail"),
    ("Root of tail", "L_Hip"), ("L_Hip", "L_Knee"), ("L_Knee", "L_B_Paw"),
    ("Root of tail", "R_Hip"), ("R_Hip", "R_Knee"), ("R_Knee", "R_B_Paw"),
]

#: 도메인 registry(`daengs_gait.contract.POSE_MODEL_V4`)와 같은 값이어야 합니다 —
#: `tests/test_gait_pose_model.py` 가 둘을 대조합니다.
MODEL_ID = "rtmpose_ap10k_ssd"
MODEL = {
    "label": "RTMPose AP-10K + ssdlite 박스 (17kp)",
    "short": "RTM+ssd",
    "joints": AP10K_NAMES,
    "priority": ["L_Hip", "L_Knee", "L_B_Paw", "R_Hip", "R_Knee", "R_B_Paw"],
    "skeleton": AP10K_LINKS_HIND,
    "kp_conf": 0.30, "min_confident_kp": 6, "spread_check": False, "bbox_frac_range": (0.0, 0.65),
    # walk_demo README §11-10 — 문자열은 옛 gait_v4/config.py 와 **한 글자도 다르지 않습니다**
    # (`model_meta()` 전체가 overlay parity 픽스처의 동일성 검사 대상입니다).
    "desc": "RTMPose 관절망(발 정밀도 최고)에 SuperAnimal ssdlite 검출기를 붙인 조합 — 후면 검출 100%. README §11-10.",
}

# v4 는 유효 프레임이 적을 때(80 미만) legacy 문구 뒤에 근거 구간을 덧붙입니다 —
# 계산·임계값은 legacy 와 완전히 같고 문구만 다릅니다(D-063 5C, `quality_gate.check_quality`
# 의 `low_tier_note` 인자로 넘깁니다).
LOW_TIER_NOTE = (
    "분석은 가능하지만 유효 프레임이 적어(§21 기준 80프레임 미만) 비교 결과의 "
    "신뢰도가 낮을 수 있습니다."
)

#: v4 는 legacy 와 달리 `build_features(feature_version=...)` 를 켭니다 — 값이 바뀌면
#: 두 기록의 비교에서 `version_warning` 이 붙습니다.
FEATURE_VERSION = "v2-p90p10-added-20260904"


def model_meta() -> dict:
    m = MODEL
    return {
        "id": MODEL_ID, "label": m["label"], "short": m["short"], "joints": list(m["joints"]),
        "priority": list(m["priority"]), "skeleton": [list(e) for e in m["skeleton"]],
        "kp_conf": m["kp_conf"], "desc": m["desc"],
    }
