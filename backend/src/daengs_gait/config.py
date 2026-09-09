"""보행 분석 설정 — 임계값 상수와 가중치 경로를 여기 한 곳에 모읍니다.

원본은 `YH-KIKI/walk_demo` 의 실험 스크립트 네 개에 흩어져 있던 값들입니다
(`e3_common.py` · `e13_external_video_pilot.py` · `e14_gait_segment_filter.py` ·
`keypoint_extractor.py`). 그 파일들은 이름·docstring 상 "실험 스크립트"인데 실제로는
production 코드가 import 하고 있었고, 동시에 module 최상단에서 `pandas` · `scipy` ·
`scikit-learn` 과 다른 실험 모듈까지 끌고 왔습니다. 알고리즘에는 필요 없는 의존성이라
**실제로 쓰는 상수·함수만 뽑아** 이쪽으로 옮겼습니다.

⚠️ **아래 임계값은 전부 walk_demo 에서 실측으로 정해진 값입니다.** 결과를 보기 전에
   고정하고 사후 조정하지 않는다는 원칙으로 잡힌 것이라, 여기서 임의로 바꾸면 그 검증이
   통째로 무의미해집니다. 바꿔야 한다면 `GAIT_FILTER_VERSION` 을 함께 올리세요 —
   그 값이 다르면 같은 영상이라도 비교 결과가 달라집니다 (`pipeline.compare_records`
   가 이 값으로 경고를 붙입니다).
"""

from __future__ import annotations

import os
from pathlib import Path

# backend/ 프로젝트 루트. `backend/src/daengs_gait/config.py` 에서 세 단계 위입니다.
#
# ⚠️ **로컬 개발에서만 쓰는 기본값의 기준점입니다.** 컨테이너에서는 아래 두 경로를
#    `GAIT_RELEASE_DIR` · `GAIT_DATA_DIR` 이 덮어쓰므로 이 값이 쓰이지 않습니다
#    (compose 가 `/models/release` · `/data` 를 넘깁니다).
#    폴더 깊이를 바꾸면 여기도 같이 고쳐야 합니다 — 안 고치면 예외 없이 엉뚱한 곳에
#    `_models/` 와 `_data/` 가 생깁니다.
ROOT = Path(__file__).resolve().parents[2]


# ──────────────────────────────────────────────────────────────────
# 가중치 경로
#
# ⚠️ **두 파일 모두 저장소에 없습니다.** walk_demo 에서도 `.gitignore` 에 걸려 있어
#    `git clone` 만으로는 안 따라옵니다 — 파일을 따로 서버에 가져다 두어야 합니다.
#    피부 병변 스크리닝의 `SCREENING_RELEASE_DIR`(D-022 · D-024)과 같은 방식입니다.
#
# 원본 walk_demo 는 이 경로를 `Path(__file__)` 기준 상대경로로 **하드코딩**하고 있었고
# (`keypoint_extractor.DEFAULT_KEYPOINT_WEIGHTS`, `crop_assist.GENERAL_MODEL_PATH`),
# 그 경로에는 한글 폴더명과 원본 데이터셋 구조가 그대로 박혀 있었습니다. 서비스에서는
# 배포 폴더 바깥의 아무 위치나 가리킬 수 있어야 하므로 환경변수로 뺐습니다.
# ──────────────────────────────────────────────────────────────────
RELEASE_DIR = Path(os.environ.get("GAIT_RELEASE_DIR") or (ROOT / "_models" / "release"))

# 반려견 전용 12-keypoint pose 모델 (YOLOv8m-pose, nc=1, kpt_shape=[12,3]).
# walk_demo 원본 경로: data/2.AI학습모델파일/키포인트/best-pth/weights/best.pt (약 50.7MB)
POSE_WEIGHTS = Path(os.environ.get("GAIT_POSE_WEIGHTS") or (RELEASE_DIR / "best.pt"))

# crop-assist 용 범용 검출기 (YOLOv8n, COCO 80-class 원본 — 파인튜닝하지 않았습니다).
# walk_demo 원본 경로: models/pretrained/yolov8n.pt (약 6.2MB)
DETECTOR_WEIGHTS = Path(
    os.environ.get("GAIT_DETECTOR_WEIGHTS") or (RELEASE_DIR / "yolov8n.pt")
)


# ──────────────────────────────────────────────────────────────────
# 데이터 경로 (분석 기록 · overlay 영상 · 업로드 원본)
#
# 기록은 아직 JSON 파일입니다 — walk_demo 의 동작을 그대로 옮긴 것입니다.
# DAENGS 의 PostgreSQL 로 옮길지는 아직 정하지 않았습니다 (README 의 TBD 참고).
# ──────────────────────────────────────────────────────────────────
# ──────────────────────────────────────────────────────────────────
# 앱이 보는 경로 접두사
#
# FastAPI 안의 경로는 `/analyze` · `/records/…` 인데, 앱이 부르는 주소에는 `/gait` 가
# 앞에 붙습니다 — **nginx 가 그것을 떼고 넘기기 때문**입니다
# (`nginx/default.conf` 의 `rewrite ^/gait/(.*)$ /$1 break`).
#
# 응답에 담는 URL(`overlay_url`)은 **앱 기준**이어야 그대로 쓸 수 있으므로 여기서 붙입니다.
#
# ⚠️ nginx 의 `location /gait/` 를 바꾸면 이 값도 같이 바꿔야 합니다. 소스에 박지 않고
#    환경변수로 둔 이유가 그것입니다 — 박혀 있으면 그때 **조용히 틀립니다**
#    (앱이 404 나는 URL 을 받는데 서버 로그에는 아무 문제도 안 보입니다).
# ──────────────────────────────────────────────────────────────────
PUBLIC_PREFIX = os.environ.get("GAIT_PUBLIC_PREFIX", "/gait").rstrip("/")


DATA_DIR = Path(os.environ.get("GAIT_DATA_DIR") or (ROOT / "_data"))
RECORDS_DIR = DATA_DIR / "records"
OVERLAYS_DIR = DATA_DIR / "overlays"
UPLOADS_DIR = DATA_DIR / "uploads"


# ──────────────────────────────────────────────────────────────────
# 업로드 크기 제한
#
# skin-screening 의 12MB(사진 한 장)를 그대로 쓰지 않습니다. 영상은 사진과
# 자릿수가 다릅니다 — 실측: 파일럿 원본 클립 0.5~3.3MB, 그러나 실제 Android
# 1080p 촬영본은 20~30초 기준 고비트레이트에서 수십~150MB 대까지 나옵니다.
#
# 기본값 150MB 는 그 상한에 여유를 둔 값입니다. `nginx/default.conf` 의
# `location /gait/` 가 이미 `client_max_body_size 200m` 로 바깥 상한을 잡아
# 두었으므로, 이 값은 그보다 낮게 유지해야 앱이 먼저 (nginx 의 맨 HTML 대신)
# 이유가 담긴 JSON 413 을 돌려줍니다. 둘 중 하나를 바꾸면 다른 쪽도 확인하세요.
# ──────────────────────────────────────────────────────────────────
MAX_UPLOAD_BYTES = int(os.environ.get("GAIT_MAX_UPLOAD_BYTES") or 150 * 1024 * 1024)


# ──────────────────────────────────────────────────────────────────
# keypoint 정의 (원본: e3_common.KEYPOINT_NAMES)
#
# ⚠️ **순서가 곧 모델 출력의 인덱스입니다.** 학습된 12kp 모델이 이 순서로 좌표를 내므로
#    한 줄이라도 바꾸면 관절 이름이 전부 어긋납니다. 다른 모든 모듈이 이 순서를 기준으로 합니다.
# ──────────────────────────────────────────────────────────────────
KEYPOINT_NAMES = [
    "Ear",
    "Acromion/Greater tubercle",
    "Dorsal scapular spine",
    "Lateral humeral epicondyle",
    "Ulnar styloid process",
    "Distal lateral aspect of fifth metacarpal bone",
    "T13 Spinous precess",
    "Iliac crest",
    "Femoral greater trochanter",
    "Femorotibial joint",
    "Lateral malleolus of the distal tibia",
    "Distal lateral aspect of the fifth metatarsus",
]
NUM_KEYPOINTS = len(KEYPOINT_NAMES)


# ──────────────────────────────────────────────────────────────────
# 추론 파라미터 (원본: e13_external_video_pilot)
# ──────────────────────────────────────────────────────────────────
# 원본 24~30fps 클립을 5fps 로 서브샘플합니다. 계산량을 줄이면서도 §21 의 프레임 수
# 기준(80프레임)을 여유롭게 넘기는 값입니다.
TARGET_FPS = 5.0
# 개체 검출 confidence 하한.
CONF_THRESH = 0.30
# 프레임 안의 개별 keypoint 를 "검출됨"으로 볼 confidence 하한.
KP_MIN_CONF = 0.30


# ──────────────────────────────────────────────────────────────────
# 보행 프레임 필터 임계값 (원본: e14_gait_segment_filter)
#
# 규칙 요약 — 아래를 **전부** 통과해야 `gait_usable` 입니다:
#   1) 개 검출 (conf >= CONF_THRESH)
#   2) 관절 충분히 보임 (conf >= KP_MIN_CONF 인 keypoint 가 MIN_CONFIDENT_KP 개 이상)
#   3) bbox 크기가 정상 범위 (너무 멀지도, 클로즈업으로 잘리지도 않음)
#   4) keypoint 가 한 군데에 뭉쳐 있지 않음
#   5) 정지 상태가 아님 (앉아 있기 · 쓰다듬는 장면 등)
# ──────────────────────────────────────────────────────────────────
MIN_CONFIDENT_KP = 6

# 화면 대비 bbox 넓이 비율. 하한 미만은 너무 멀어 신뢰 불가, 상한 초과는 클로즈업이라
# 몸이 화면 밖으로 잘렸을 가능성이 큽니다.
MIN_BBOX_FRAC = 0.03
MAX_BBOX_FRAC = 0.65

# 정지 판정.
#
# ⚠️ **"거리"가 아니라 "속도"로 봅니다.** 처음에는 인접 프레임 간 이동 거리를 고정 비율로
#    비교했는데(STATIONARY_DISP_FRAC), 그 값은 5fps 인접 프레임(약 0.2초 간격)에서 나온
#    기준이라 30fps 영상(약 0.033초 간격)에 그대로 쓰면 실제 이동량이 물리적으로 훨씬
#    작을 수밖에 없어 정지 오탐이 급증했습니다 (실측: usable 128 → 93, 정지 229 → 264).
#    그래서 경과 시간으로 나눠 fps 와 무관한 기준으로 바꿨습니다.
STATIONARY_DISP_FRAC = 0.02  # 하위호환 표기용. 실제 판정에는 쓰지 않습니다.
# = 0.1/초. 5fps 의 인접 프레임 간격(0.2초)에서 위 값과 정확히 같은 결과가 나오도록 역산한 값입니다.
STATIONARY_SPEED_FRAC_PER_SEC = STATIONARY_DISP_FRAC / 0.2
# 이웃과 이 시간(초)보다 멀리 떨어져 있으면(중간에 검출이 끊긴 경우 등) 평균 속도가
# 순간 이동 여부를 대변하지 못하므로 비교 자체를 포기합니다.
STATIONARY_MAX_GAP_SEC = 0.6

# keypoint 뭉침 방지 (2026-08-25 추가).
# confidence 는 높은데 12개 keypoint 가 몸 전체에 퍼지지 않고 한 군데(목 · 하네스 등)에
# 뭉쳐 찍히는 오탐을 막습니다. 실측 2건(정상 보행 0.47 / 앉아서 헐떡이는 오탐 0.12~0.17)
# 기준의 잠정치입니다 — 표본이 적어 추후 재검증 대상입니다.
MIN_KP_SPREAD_RATIO = 0.30

# 품질 플래그. 제외 사유가 아니라 **기록만** 합니다.
NIGHT_BRIGHTNESS_THRESH = 60.0   # 그레이스케일 평균 밝기
BLUR_VAR_THRESH = 80.0           # Laplacian 분산
FAR_BBOX_FRAC_THRESH = 0.08      # MIN_BBOX_FRAC(0.03)보다 느슨한 참고용 임계치

# ⚠️ **위 임계값을 하나라도 바꾸면 이 문자열을 반드시 올리세요.**
# 같은 영상이라도 이 버전이 다르면 어떤 프레임을 유효로 볼지 기준 자체가 달라져
# 관절 이동범위가 달라 보입니다 (실측 확인 — 같은 영상을 구/신 버전으로 분석했더니
# 5개 관절 중 4개에서 "세로 차이 관찰됨"이 떴는데 실제로는 같은 영상이었습니다).
# `pipeline.compare_records` 가 두 기록의 이 값을 대조해 경고를 붙입니다.
GAIT_FILTER_VERSION = "v5-stationary-speed-based-20260826"


# ──────────────────────────────────────────────────────────────────
# crop-assist (원본: gait_demo/crop_assist)
#
# 전용 12kp 모델이 전체 프레임에서 개를 못 찾거나 관절을 충분히 못 잡을 때만 도는
# 2단계 보정입니다. 범용 모델로 위치를 먼저 잡고 그 주변을 확대해 전용 모델을 재적용합니다.
# ──────────────────────────────────────────────────────────────────
COCO_DOG_CLASS_ID = 16
# 전용 모델보다 낮게 잡습니다 — "대략 어디 있는지"만 필요하고 정밀 keypoint 는 2단계에서 냅니다.
GENERAL_CONF_THRESH = 0.15
# bbox 각 방향으로 (너비/높이) * 0.4 만큼 여유. 다리·꼬리가 잘리는 것을 막습니다.
MARGIN_RATIO = 0.4
# crop 을 이 크기로 업스케일합니다 (비율 유지, 이미 크면 확대하지 않음).
TARGET_LONG_SIDE = 640


# ──────────────────────────────────────────────────────────────────
# 품질 게이트 (원본: gait_demo/quality_gate)
# ──────────────────────────────────────────────────────────────────
# 뒤 단계(비교)의 interleaved half-split 이 성립하려면 반씩 최소 2장이 필요합니다.
# 그럴듯하게 정한 숫자가 아니라 계산이 가능한 최소치입니다.
MIN_USABLE_FRAMES = 4


# ──────────────────────────────────────────────────────────────────
# feature (원본: e3_trajectory_features)
# ──────────────────────────────────────────────────────────────────
MIN_FRAMES_FOR_PERIODICITY = 8

# 슬개골 관련 관절 체인(해부학적으로 확인된 것). trajectory 와 UI 요약이 이 목록을 씁니다.
# 원본 `e3_trajectory_features.CHAINS["Back"]` 과 같습니다.
PRIORITY_JOINTS = [
    "Iliac crest",
    "Femoral greater trochanter",
    "Femorotibial joint",
    "Lateral malleolus of the distal tibia",
    "Distal lateral aspect of the fifth metatarsus",
]

# overlay 가 그리는 연결선. 원본 `e3_trajectory_features.CHAINS["Left"]`(측면 전신 뷰의
# 해부학적 인접 체인)와 같은 순서입니다.
SKELETON_CHAIN = [
    "Ear",
    "Dorsal scapular spine",
    "Acromion/Greater tubercle",
    "Lateral humeral epicondyle",
    "Ulnar styloid process",
    "Distal lateral aspect of fifth metacarpal bone",
    "T13 Spinous precess",
    "Iliac crest",
    "Femoral greater trochanter",
    "Femorotibial joint",
    "Lateral malleolus of the distal tibia",
    "Distal lateral aspect of the fifth metatarsus",
]

# 비교에서 "차이 관찰됨"으로 부를 최소선.
# ⚠️ **검증된 임상 기준이 아닙니다.** 소수점 수치를 그대로 노출하면 과신을 부른다고 판단해
#    정성적 방향(차이 관찰됨 / 비슷함)만 내보내기로 하면서 편의상 잡은 잠정 기준선입니다.
COMPARE_DIFF_THRESHOLD = 0.30


# ──────────────────────────────────────────────────────────────────
# 이 엔진의 pose model ID (D-063)
#
# 기록에 "어떤 관절 정의로 만들어졌나"를 남기는 메타데이터입니다. v4 는 자기 record 에
# `pose_model`(= gait_v4.config.MODEL_ID) 을 이미 넣는데, 이 엔진은 ID 가 없어서 여기서
# 정합니다. 값의 정본과 관절 집합은 `daengs_gait.contract` 에 있습니다 — 바꾸려면 거기와
# 백필 SQL 을 같이 바꿔야 하고, 테스트가 셋을 대조합니다.
# ──────────────────────────────────────────────────────────────────
POSE_MODEL_ID = "yolov8_12kp_best"
