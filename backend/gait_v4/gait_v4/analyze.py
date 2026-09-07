# -*- coding: utf-8 -*-
"""영상 1개 → gait record — walk_demo src/gait_demo/pipeline.process_video 에서 저장소·overlay 경로 결정만 뺀 것.
반환 record 의 필드는 walk_demo 기록(JSON)과 같다(record_id 는 None — 저장소가 부여). overlay 는 out 경로를 주면 만든다."""
from datetime import datetime, timezone
from pathlib import Path

from .config import GAIT_FILTER_VERSION, MIN_KP_SPREAD_RATIO, MODEL, MODEL_ID, model_meta
from .features import build_features
from .gait_filter import apply_gait_filter
from .pose import run_pose
from .quality import check_quality
from .trajectory import build_trajectories


def analyze_video(video_path, follow_cam: bool = False, overlay_out=None,
                  date: str = None, note: str = None, dog_id: str = None):
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
    quality = check_quality(records)

    record = {
        "record_id": None,
        "source_file": video_path.name,
        "date": date,
        "note": note,
        "dog_id": dog_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
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
        from .overlay import render_overlay_video
        record["overlay_video"] = render_overlay_video(video_path, records, overlay_out, model_meta=mm)

    record["trajectories"] = build_trajectories(records, joints=mm["priority"], min_conf=mm["kp_conf"])
    record["features"] = build_features(records, priority_joints=mm["priority"])
    return record, records
