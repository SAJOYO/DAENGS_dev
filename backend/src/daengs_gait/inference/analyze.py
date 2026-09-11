"""영상 1개 → v4 gait record (#304, D-063 5B).

`backend/gait_v4/gait_v4/analyze.py` 의 이식입니다 — pose 추론(`run_pose`)은 이 패키지
안(`daengs_gait.inference.pose`)에 그대로 남지만, gait 필터·품질·궤적·feature·overlay 는
**5C 에서 이미 공유 모듈로 옮긴** `daengs_gait` 의 계산을 v4 모드 인자로 호출합니다 —
같은 계산을 두 벌 두지 않습니다.

반환 record 의 필드는 옛 `gait_v4.analyze_video` 와 같습니다(`record_id` 는 None —
저장소가 부여). overlay 는 out 경로를 주면 만듭니다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

#: `daengs_gait.config.GAIT_FILTER_VERSION` 과 같은 값이어야 합니다 — 5C 에서 legacy·v4
#: 임계값이 통일됐으므로 같은 상수를 그대로 씁니다.
from daengs_gait.config import GAIT_FILTER_VERSION, MIN_KP_SPREAD_RATIO
from daengs_gait.features import build_features
from daengs_gait.gait_filter import apply_gait_filter
from daengs_gait.inference.model import FEATURE_VERSION, LOW_TIER_NOTE, MODEL, MODEL_ID, model_meta
from daengs_gait.inference.pose import run_pose
from daengs_gait.quality_gate import check_quality
from daengs_gait.trajectory import build_trajectories


def analyze_video(
    video_path, follow_cam: bool = False, overlay_out=None,
    date: str | None = None, note: str | None = None, dog_id: str | None = None,
):
    """returns (record: dict, frame_records: list). quality.status != 'ok' 면 features/trajectories 없이 끝낸다."""
    video_path = Path(video_path)
    mm = model_meta()
    cfg = MODEL

    records, meta = run_pose(video_path)
    records = apply_gait_filter(
        records, meta["diag"], min_confident_kp=cfg["min_confident_kp"], sample_fps=meta["sample_fps"],
        spread_ratio=MIN_KP_SPREAD_RATIO if cfg["spread_check"] else None,
        stationary_check=not follow_cam,
        bbox_frac_range=cfg.get("bbox_frac_range", (0.03, 0.65)),
    )
    quality = check_quality(records, low_tier_note=LOW_TIER_NOTE)

    record = {
        "record_id": None,
        "source_file": video_path.name,
        "date": date,
        "note": note,
        "dog_id": dog_id,
        "created_at": datetime.now(UTC).isoformat(),
        "pose_model": MODEL_ID,
        "pose_model_label": mm["label"],
        "pose_model_meta": {"priority": mm["priority"], "kp_conf": mm["kp_conf"], "n_joints": len(mm["joints"])},
        "group_id": None,
        "follow_cam": bool(follow_cam),
        "timing": {"pose_elapsed_sec": meta.get("elapsed_sec"), "superanimal": meta.get("superanimal"),
                   "ssd_backend": meta.get("ssd_backend"), "detect_sec": meta.get("detect_sec")},
        "lr_fix": meta.get("lr_fix"),
        "video_meta": {
            "resolution": f"{meta['width']}x{meta['height']}",
            "native_fps": round(meta["native_fps"], 2),
        },
        "quality": quality,
        "gait_filter_version": GAIT_FILTER_VERSION,
    }

    if quality["status"] != "ok":
        return record, records

    if overlay_out:
        from daengs_gait.overlay import render_overlay_video

        record["overlay_video"] = render_overlay_video(video_path, records, overlay_out, model_meta=mm)

    record["trajectories"] = build_trajectories(records, joints=mm["priority"], min_conf=mm["kp_conf"])
    record["features"] = build_features(
        records, priority_joints=mm["priority"], p90p10=True, feature_version=FEATURE_VERSION,
    )
    return record, records
