"""분석 가능 여부 판정.

새 기준을 만들지 않습니다 — `gait_filter.apply_gait_filter` 가 이미 프레임별로 매긴
`gait_usable` / `exclude_reason` / `quality_flags` 를 **집계만** 합니다.

`MIN_USABLE_FRAMES` 는 뒤 단계(비교)의 half-split 이 성립하는 최소치이고,
`quality_tier` 구간은 재현성 실측(0.56~0.81)에서 나온 프레임 수 구간을 그대로 씁니다.
둘 다 여기서 새로 정한 숫자가 아닙니다.
"""

from __future__ import annotations

from daengs_gait.config import MIN_USABLE_FRAMES


def check_quality(records: list) -> dict:
    n_sampled = len(records)
    n_detected = sum(1 for r in records if r["detected"])
    n_usable = sum(1 for r in records if r.get("gait_usable"))

    reasons: dict = {}
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
        # 여기서 끝냅니다. 검출 안 된 것을 억지로 feature 로 만들지 않습니다.
        return {
            "status": "unavailable",
            "reason": "분석 가능한 보행 프레임이 부족합니다.",
            "recommendation": "밝은 환경에서 강아지 전신이 보이도록, 흔들림 없이 다시 촬영해 주세요.",
            **stats,
        }

    # 재현성 실측의 프레임 수 구간을 그대로 씁니다 — 참고용 표시일 뿐 새 등급 기준이 아닙니다.
    if n_usable > 80:
        tier = "good"
    elif n_usable >= 20:
        tier = "ok"
    else:
        tier = "low"

    return {
        "status": "ok",
        # unavailable 과 키 구조를 맞춥니다 — 프론트가 항상 같은 필드로 처리할 수 있게.
        "reason": None,
        "recommendation": None,
        "quality_tier": tier,
        "quality_note": (
            "분석은 가능하지만 유효 프레임이 적어 비교 결과의 신뢰도가 낮을 수 있습니다."
            if tier != "good"
            else None
        ),
        **stats,
    }
