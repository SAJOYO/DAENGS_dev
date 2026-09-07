# -*- coding: utf-8 -*-
"""분석 가능 여부 품질 체크 — walk_demo src/gait_demo/quality_gate.py 그대로.
apply_gait_filter 가 매긴 gait_usable/exclude_reason/quality_flags 를 집계만 한다.
MIN_USABLE_FRAMES=4 는 뒤 단계(비교)가 계산 가능한 최소치, tier 구간(<=10/11-20/21-40/41-80/>80)은 §21 재현성 구간 재사용."""
from .config import MIN_USABLE_FRAMES


def check_quality(records: list) -> dict:
    n_sampled = len(records)
    n_detected = sum(1 for r in records if r["detected"])
    n_usable = sum(1 for r in records if r.get("gait_usable"))

    reasons = {}
    for r in records:
        if r["detected"] and not r.get("gait_usable"):
            reason = r.get("exclude_reason") or "unknown"
            reasons[reason] = reasons.get(reason, 0) + 1

    n_night = sum(1 for r in records if "night" in (r.get("quality_flags") or []))
    n_blur = sum(1 for r in records if "blur" in (r.get("quality_flags") or []))

    stats = {
        "n_frames_sampled": n_sampled,
        "n_frames_detected": n_detected,
        "n_frames_gait_usable": n_usable,
        "detection_rate": round(n_detected / n_sampled, 3) if n_sampled else 0.0,
        "gait_usable_rate": round(n_usable / n_sampled, 3) if n_sampled else 0.0,
        "exclude_reason_counts": reasons,
        "n_frames_flagged_night": n_night,
        "n_frames_flagged_blur": n_blur,
    }

    if n_usable < MIN_USABLE_FRAMES:
        return {
            "status": "unavailable",
            "reason": "분석 가능한 보행 프레임이 부족합니다.",
            "recommendation": "밝은 환경에서 강아지 전신이 보이도록, 흔들림 없이 다시 촬영해 주세요.",
            **stats,
        }

    if n_usable > 80:
        tier = "good"
    elif n_usable >= 20:
        tier = "ok"
    else:
        tier = "low"

    return {
        "status": "ok",
        "reason": None,
        "recommendation": None,
        "quality_tier": tier,
        "quality_note": (
            "분석은 가능하지만 유효 프레임이 적어(§21 기준 80프레임 미만) 비교 결과의 "
            "신뢰도가 낮을 수 있습니다." if tier != "good" else None
        ),
        **stats,
    }
