"""보행 엔진 출력 계약 — 엔진이 실제로 내는 모양을 **적어 둔 것**입니다.

지금 분석을 만드는 엔진은 v4 하나지만(6단계), 레지스트리는 **옛 legacy 기록까지** 다룹니다 —
그 기록을 읽고 비교하는 일은 추론 runtime 이 있느냐와 무관합니다.

새 추상화가 아닙니다. `daengs_backend.services.gait._run_analysis` 가 읽는 키와
`gait_records` 컬럼으로 가는 값을 그대로 이름 붙였습니다 (D-063). 여기에 없는 키는
엔진이 더 내도 됩니다 — v4 의 `timing`·`lr_fix`·`trajectories` 처럼 backend 가 안 쓰는
것은 검사가 무시합니다.

⚠️ 이 파일은 **가벼워야 합니다.** numpy 도 cv2 도 torch 도 import 하지 않습니다.
   backend 웹 프로세스가 (함수 안에서) 부를 수 있어야 하고, 하네스·테스트가 DB 없이
   읽어야 합니다.

## pose_model

기록이 **어떤 pose model / 관절 정의로** 만들어졌는지를 나타내는 메타데이터입니다.
지금 어떤 엔진을 실행할지 고르는 설정(`GAIT_ENGINE`)이 아닙니다. 두 값의 관절 이름은
한 글자도 안 겹치므로, 저장된 `summary_for_ui` 의 관절 키만으로 옛 기록의 값을 **판별**할
수 있습니다 (`classify_joint_keys`). 추정은 하지 않습니다 — 키가 없거나 섞이면 None.
"""

from __future__ import annotations

from typing import TypedDict

# ── pose model 레지스트리 ─────────────────────────────────────────────────────
#
# 관절 목록의 원본은 config 입니다 — legacy(옛 기록용) `daengs_gait.config.KEYPOINT_NAMES`,
# v4 `daengs_gait/inference/model.py AP10K_NAMES`. tests/test_gait_pose_model.py 가 둘을
# 읽어 여기와 대조하고, `db/migrations/2026-09-09_gait_records_pose_model.sql` 과
# 그 verify 의 IN 목록도 같은 테스트가 대조합니다. **한 곳만 고치면 테스트가 빨간 줄을 냅니다.**

#: legacy — YOLOv8m-pose · nc=1 · 12 keypoint · `best.pt` (D-029). 이름은 여기서 새로 정한 것
#: (옛 엔진은 자기 ID 를 낸 적이 없습니다).
POSE_MODEL_LEGACY = "yolov8_12kp_best"
#: v4 — SuperAnimal ssdlite 박스 + RTMPose-m AP-10K 17 keypoint (#304). 값은 v4 엔진이
#: record 에 이미 넣는 `daengs_gait.inference.model.MODEL_ID` 그대로입니다.
POSE_MODEL_V4 = "rtmpose_ap10k_ssd"

LEGACY_12KP_JOINTS: frozenset[str] = frozenset(
    {
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
    }
)

AP10K_17_JOINTS: frozenset[str] = frozenset(
    {
        "L_Eye",
        "R_Eye",
        "Nose",
        "Neck",
        "Root of tail",
        "L_Shoulder",
        "L_Elbow",
        "L_F_Paw",
        "R_Shoulder",
        "R_Elbow",
        "R_F_Paw",
        "L_Hip",
        "L_Knee",
        "L_B_Paw",
        "R_Hip",
        "R_Knee",
        "R_B_Paw",
    }
)


class EngineMeta(TypedDict):
    """엔진(= pose model)이 자기 관절 정의를 말하는 모양. v4 의 `model_meta()` 와 같은 키."""

    id: str
    joints: list[str]
    priority: list[str]
    skeleton: list[tuple[str, str]]
    kp_conf: float


#: id → 관절 정의. `priority`·`skeleton` 은 각 엔진 config 의 값 그대로.
POSE_MODELS: dict[str, EngineMeta] = {
    POSE_MODEL_LEGACY: {
        "id": POSE_MODEL_LEGACY,
        "joints": [
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
        ],
        "priority": [
            "Iliac crest",
            "Femoral greater trochanter",
            "Femorotibial joint",
            "Lateral malleolus of the distal tibia",
            "Distal lateral aspect of the fifth metatarsus",
        ],
        "skeleton": [
            ("Ear", "Dorsal scapular spine"),
            ("Dorsal scapular spine", "Acromion/Greater tubercle"),
            ("Acromion/Greater tubercle", "Lateral humeral epicondyle"),
            ("Lateral humeral epicondyle", "Ulnar styloid process"),
            ("Ulnar styloid process", "Distal lateral aspect of fifth metacarpal bone"),
            ("Distal lateral aspect of fifth metacarpal bone", "T13 Spinous precess"),
            ("T13 Spinous precess", "Iliac crest"),
            ("Iliac crest", "Femoral greater trochanter"),
            ("Femoral greater trochanter", "Femorotibial joint"),
            ("Femorotibial joint", "Lateral malleolus of the distal tibia"),
            (
                "Lateral malleolus of the distal tibia",
                "Distal lateral aspect of the fifth metatarsus",
            ),
        ],
        "kp_conf": 0.30,
    },
    POSE_MODEL_V4: {
        "id": POSE_MODEL_V4,
        "joints": [
            "L_Eye",
            "R_Eye",
            "Nose",
            "Neck",
            "Root of tail",
            "L_Shoulder",
            "L_Elbow",
            "L_F_Paw",
            "R_Shoulder",
            "R_Elbow",
            "R_F_Paw",
            "L_Hip",
            "L_Knee",
            "L_B_Paw",
            "R_Hip",
            "R_Knee",
            "R_B_Paw",
        ],
        "priority": ["L_Hip", "L_Knee", "L_B_Paw", "R_Hip", "R_Knee", "R_B_Paw"],
        "skeleton": [
            ("Neck", "Root of tail"),
            ("Root of tail", "L_Hip"),
            ("L_Hip", "L_Knee"),
            ("L_Knee", "L_B_Paw"),
            ("Root of tail", "R_Hip"),
            ("R_Hip", "R_Knee"),
            ("R_Knee", "R_B_Paw"),
        ],
        "kp_conf": 0.30,
    },
}


def classify_joint_keys(keys) -> str | None:
    """관절 키 집합 → pose model id. **판별이지 추정이 아닙니다.**

    키가 1개 이상이고 **전부** 한 모델의 관절이면 그 id, 아니면 None:
    빈 집합 · 두 체계가 섞임 · 어느 집합에도 없는 키. 백필 SQL 의 CASE 와 같은 규칙이고,
    빈 집합이 `all(...)` 을 통과하지 않도록 개수를 먼저 봅니다.
    """
    joints = set(keys)
    if not joints:
        return None
    if joints <= AP10K_17_JOINTS:
        return POSE_MODEL_V4
    if joints <= LEGACY_12KP_JOINTS:
        return POSE_MODEL_LEGACY
    return None


# ── 프레임 레코드 ───────────────────────────────────────────────────────────
class FrameRecord(TypedDict, total=False):
    """5fps 로 샘플한 프레임 하나. `inference.pose._base_frame_record` 가 만들고
    `gait_filter.apply_gait_filter` 가 뒤 둘을 채웁니다.

    `crop_*` · `general_detector_conf` 는 옛 legacy crop-assist 자리입니다 — 그 runtime 은
    6단계에서 없앴지만 v4 가 같은 키를 (None 으로) 내고 **옛 기록에도 남아 있어** 계약에
    그대로 둡니다.
    """

    frame_idx: int
    detected: bool
    bbox_frac: float | None
    bbox_center: tuple[float, float] | None
    kps: list[tuple[str, float, float, float]] | None  # (name, x, y, conf) 원본 픽셀
    n_confident_kp: int
    quality_flags: list[str]
    crop_assisted: bool
    crop_box: tuple[int, int, int, int] | None
    general_detector_conf: float | None
    det_box: list[float]
    bbox_wh: tuple[float, float]
    # apply_gait_filter 뒤
    exclude_reason: str | None
    gait_usable: bool


# ── 분석 레코드 (backend 가 저장하는 것) ──────────────────────────────────────
class QualityBlock(TypedDict, total=False):
    status: str  # "ok" | "unavailable"
    reason: str | None
    recommendation: str | None
    quality_tier: str  # status=ok 일 때만: good | ok | low
    quality_note: str | None
    n_frames_sampled: int
    n_frames_detected: int
    n_frames_gait_usable: int
    detection_rate: float
    gait_usable_rate: float
    exclude_reason_counts: dict[str, int]
    n_frames_flagged_night: int
    n_frames_flagged_blur: int


class FeaturesBlock(TypedDict, total=False):
    summary_for_ui: dict[str, dict[str, float | None]]  # 관절 → {x_range, y_range, (p90p10…)}
    internal_feature_vector: dict[str, float]  # 비교 전용. API 로 나가면 안 됩니다
    n_frames_used: int
    feature_version: str  # v4 만


class VideoMeta(TypedDict):
    resolution: str  # "1080x1920"
    native_fps: float


class AnalysisRecord(TypedDict, total=False):
    """`_run_analysis` 가 `gait_records` 로 옮기는 것. 두 엔진이 같은 키를 냅니다."""

    pose_model: str
    quality: QualityBlock
    gait_filter_version: str
    video_meta: VideoMeta
    features: FeaturesBlock  # quality.status == "ok" 일 때만
    overlay_video: str | None  # 경로. 없거나 인코딩 실패면 None/부재


_QUALITY_STATS = (
    "n_frames_sampled",
    "n_frames_detected",
    "n_frames_gait_usable",
    "detection_rate",
    "gait_usable_rate",
    "exclude_reason_counts",
    "n_frames_flagged_night",
    "n_frames_flagged_blur",
)


def check_analysis_record(record) -> list[str]:
    """계약 위반 목록. 비어 있으면 통과.

    **예외를 내지 않습니다.** 워커가 이것을 이유로 분석을 실패시키면 안 되기 때문입니다 —
    사고를 만드는 쪽이 아니라 발견하는 쪽입니다. 워커는 경고 로그로만 남기고, 테스트가
    빈 목록을 단언합니다.
    """
    problems: list[str] = []
    if not isinstance(record, dict):
        return [f"record 가 dict 가 아님: {type(record).__name__}"]

    pose_model = record.get("pose_model")
    if not isinstance(pose_model, str) or not pose_model:
        problems.append("pose_model 이 없거나 문자열이 아님")
    elif pose_model not in POSE_MODELS:
        problems.append(f"pose_model 이 레지스트리에 없음: {pose_model!r}")

    quality = record.get("quality")
    if not isinstance(quality, dict):
        problems.append("quality 가 dict 가 아님")
        quality = {}
    status = quality.get("status")
    if status not in ("ok", "unavailable"):
        problems.append(f"quality.status 가 ok/unavailable 이 아님: {status!r}")
    for key in _QUALITY_STATS:
        if key not in quality:
            problems.append(f"quality.{key} 없음")
    if status == "ok":
        if quality.get("quality_tier") not in ("good", "ok", "low"):
            problems.append(
                f"quality.quality_tier 가 good/ok/low 가 아님: {quality.get('quality_tier')!r}"
            )
        features = record.get("features")
        if not isinstance(features, dict):
            problems.append("status=ok 인데 features 가 없음")
        else:
            summary = features.get("summary_for_ui")
            vector = features.get("internal_feature_vector")
            if not isinstance(summary, dict):
                problems.append("features.summary_for_ui 가 dict 가 아님")
            elif pose_model in POSE_MODELS:
                foreign = set(summary) - set(POSE_MODELS[pose_model]["joints"])
                if foreign:
                    problems.append(
                        f"summary_for_ui 에 {pose_model} 의 관절이 아닌 키: {sorted(foreign)}"
                    )
            if not isinstance(vector, dict):
                problems.append("features.internal_feature_vector 가 dict 가 아님")

    if not isinstance(record.get("gait_filter_version"), str):
        problems.append("gait_filter_version 이 문자열이 아님")

    meta = record.get("video_meta")
    if not isinstance(meta, dict) or "resolution" not in meta or "native_fps" not in meta:
        problems.append("video_meta 에 resolution/native_fps 가 없음")

    overlay = record.get("overlay_video", None)
    if overlay is not None and not isinstance(overlay, str):
        problems.append("overlay_video 가 경로 문자열도 None 도 아님")
    return problems
