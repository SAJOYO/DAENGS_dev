# -*- coding: utf-8 -*-
"""두 기록 비교 — walk_demo src/gait_demo/pipeline.compare_records 와 동일 로직.
차이점 하나: record_id 로 저장소를 읽는 대신 **record dict 두 개를 직접 받는다**(dev 저장소는 별도).

판정 규칙(README §11-15, 2026-09-04): 관절을 좌/우로 나눠 3관절(엉덩이·무릎·발) 중 2개 이상이 다르면(가로 또는 세로 상대차 >30%)
  한쪽만 → one_side(추적 관찰 권장) / 양쪽 → both_sides(촬영 조건 확인) / 없음 → no_change.
  조건 불일치(follow-cam·유효 프레임<80·해상도·분석 버전)는 condition_flags 로 덧붙인다.
message_for_ui 문구는 서비스 화면에서 바꿔도 된다 — message_kind / side_summary / condition_flags 가 계약이다.
알려진 한계: max−min 지표라 극값 1프레임에 판정이 뒤집힐 수 있다(KNOWN_LIMITATIONS §2).
"""
import numpy as np

DIFF_THRESHOLD = 0.30   # "확실히 다르다고 부를 최소선" — 검증된 임상 기준 아님
SIDE_MIN_DIFF = 2


def _direction_note(va, vb):
    if va is None or vb is None:
        return "비교 불가(한쪽 기록에 없음)"
    denom = max(abs(va), abs(vb), 1e-9)
    rel_diff = abs(va - vb) / denom
    return "차이 관찰됨" if rel_diff > DIFF_THRESHOLD else "비슷함"


def _side_of(joint):
    n = joint.lower()
    if joint.startswith("L_") or "left" in n:
        return "왼쪽"
    if joint.startswith("R_") or "right" in n:
        return "오른쪽"
    return "전체"


def compare_records(a: dict, b: dict) -> dict:
    """a, b: analyze.analyze_video() 가 돌려준 record dict (quality/features/follow_cam/video_meta/gait_filter_version 포함)."""
    pm_a, pm_b = a.get("pose_model", "best_pt"), b.get("pose_model", "best_pt")
    if pm_a != pm_b:
        return {
            "status": "unavailable",
            "reason": f"비교 불가 - 두 기록의 pose 모델이 다릅니다(A: {pm_a} / B: {pm_b}). 관절 정의가 달라 같은 모델끼리만 비교할 수 있습니다.",
            "recommendation": "같은 모델로 분석된 기록을 고르세요.",
        }

    for r in (a, b):
        if r["quality"]["status"] != "ok":
            return {
                "status": "unavailable",
                "reason": f"비교 불가 - 기록 {r.get('record_id')}가 분석 가능 상태가 아닙니다.",
                "recommendation": r["quality"].get("recommendation"),
            }

    joint_comparison = {}
    sa = a["features"]["summary_for_ui"]
    sb = b["features"]["summary_for_ui"]
    for joint in sorted(set(sa) | set(sb)):
        ja, jb = sa.get(joint), sb.get(joint)
        joint_comparison[joint] = {
            "record_a": ja,
            "record_b": jb,
            "comparison_note": {
                "x": _direction_note(ja.get("x_range") if ja else None, jb.get("x_range") if jb else None),
                "y": _direction_note(ja.get("y_range") if ja else None, jb.get("y_range") if jb else None),
            },
        }

    low_tier_records = [r.get("record_id") for r in (a, b) if r["quality"].get("quality_tier") != "good"]
    reliability_note = (
        "두 기록 모두 유효 프레임 수가 적어(§21 기준 80프레임 미만) 비교 결과는 참고용입니다."
        if len(low_tier_records) == 2 else
        f"기록 {low_tier_records[0]}은(는) 유효 프레임 수가 적어(§21 기준 80프레임 미만) "
        f"비교 결과가 참고용에 가깝습니다." if low_tier_records else None
    )

    ver_a = a.get("gait_filter_version")
    ver_b = b.get("gait_filter_version")
    if ver_a != ver_b:
        version_warning = (
            f"두 기록이 서로 다른 분석 버전으로 만들어졌습니다"
            f"(A: {ver_a or '기록 없음(구버전)'} / B: {ver_b or '기록 없음(구버전)'}) - "
            f"같은 영상이라도 버전이 다르면 어떤 프레임을 유효로 볼지 기준 자체가 달라져 "
            f"관절 이동범위 차이가 실제 걸음 차이가 아닐 수 있습니다."
        )
    else:
        version_warning = None

    va = a["features"]["internal_feature_vector"]
    vb = b["features"]["internal_feature_vector"]
    cols = sorted(set(va) & set(vb))
    xa = np.array([va[c] for c in cols], dtype=float)
    xb = np.array([vb[c] for c in cols], dtype=float)
    mask = ~(np.isnan(xa) | np.isnan(xb))
    xa, xb = xa[mask], xb[mask]
    raw_cosine = (
        float(np.sum(xa * xb) / (np.linalg.norm(xa) * np.linalg.norm(xb) + 1e-9))
        if len(xa) > 0 else None
    )

    side_summary = {}
    for joint, v in joint_comparison.items():
        note = v["comparison_note"]
        differs = note["x"] == "차이 관찰됨" or note["y"] == "차이 관찰됨"
        s = side_summary.setdefault(_side_of(joint), {"n_joints": 0, "n_diff": 0, "diff_joints": []})
        s["n_joints"] += 1
        if differs:
            s["n_diff"] += 1
            s["diff_joints"].append(joint)
    for s in side_summary.values():
        s["flagged"] = s["n_diff"] >= SIDE_MIN_DIFF

    condition_flags = []
    if bool(a.get("follow_cam")) != bool(b.get("follow_cam")):
        condition_flags.append("두 기록의 '카메라가 따라감' 설정이 다름")
    if low_tier_records:
        condition_flags.append("유효 프레임 80 미만(정지 구간·검출 실패로 잘림)")
    if (a.get("video_meta") or {}).get("resolution") != (b.get("video_meta") or {}).get("resolution"):
        condition_flags.append("영상 해상도가 다름")
    if version_warning:
        condition_flags.append("분석 버전이 다름")

    flagged_sides = [k for k in ("왼쪽", "오른쪽", "전체") if side_summary.get(k, {}).get("flagged")]
    cond_txt = (" 다만 " + " · ".join(condition_flags) + " — 촬영 조건 차이일 수 있으니 같은 조건으로 다시 촬영해 확인해 주세요.") if condition_flags else ""
    if not flagged_sides:
        message_kind = "no_change"
        message_for_ui = "이전 기록과 비교해 뚜렷한 움직임 차이는 관찰되지 않았습니다."
    elif flagged_sides == ["전체"]:
        s = side_summary["전체"]
        message_kind = "change"
        message_for_ui = f"움직임 지표 {s['n_joints']}개 중 {s['n_diff']}개에서 차이가 관찰됩니다. 추적 관찰을 권장합니다." + cond_txt
    elif len(flagged_sides) == 1:
        k = flagged_sides[0]; s = side_summary[k]
        message_kind = "one_side"
        message_for_ui = (f"{k} 뒷다리 움직임에 차이가 관찰됩니다({s['n_joints']}개 지표 중 {s['n_diff']}개). "
                          f"추적 관찰을 권장합니다." + cond_txt)
    else:
        message_kind = "both_sides"
        message_for_ui = ("양쪽 뒷다리 모두에서 차이가 관찰됩니다. 걸음 변화보다 촬영 거리·각도·카메라 움직임 등 "
                          "촬영 조건이 달랐을 가능성이 크니 먼저 확인해 주세요." + cond_txt)

    return {
        "status": "ok",
        "reason": None,
        "recommendation": None,
        "record_a": {"record_id": a.get("record_id"), "date": a.get("date")},
        "record_b": {"record_id": b.get("record_id"), "date": b.get("date")},
        "message_for_ui": message_for_ui,
        "message_kind": message_kind,
        "side_summary": side_summary,
        "condition_flags": condition_flags,
        "reliability_note": reliability_note,
        "version_warning": version_warning,
        "diff_threshold_note": (
            f"'차이 관찰됨'은 두 값의 상대 차이가 {int(DIFF_THRESHOLD * 100)}% 이상일 때 표시됩니다"
            f"(검증된 임상 기준이 아니라 편의상 잡은 잠정 기준선입니다)."
        ),
        "joint_movement_range_comparison": joint_comparison,
        "_dev_only_joint_notes_p90p10": {
            joint: {"x": _direction_note((sa.get(joint) or {}).get("x_range_p90p10"), (sb.get(joint) or {}).get("x_range_p90p10")),
                    "y": _direction_note((sa.get(joint) or {}).get("y_range_p90p10"), (sb.get(joint) or {}).get("y_range_p90p10"))}
            for joint in sorted(set(sa) | set(sb))},
        "_dev_only_feature_versions": [a["features"].get("feature_version"), b["features"].get("feature_version")],
        "_dev_only_raw_feature_cosine": raw_cosine,       # UI 노출 금지 — "건강 점수" 로 보이면 안 됨
        "_dev_only_n_common_feature_dims": int(mask.sum()),
    }
