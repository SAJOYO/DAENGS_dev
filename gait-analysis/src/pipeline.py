"""오케스트레이션 — 영상 하나를 기록으로, 기록 둘을 비교로.

    영상 입력 → 품질 체크 → keypoint 추론 → skeleton overlay
              → trajectory → feature → 기록 저장 → (선택) 기록 비교

품질 체크에서 `unavailable` 이면 **그 뒤 단계를 전부 건너뜁니다.** 검출되지 않은 것을
억지로 feature 로 만들면 숫자는 나오지만 아무 뜻이 없고, 그 숫자가 비교에 들어가면
없는 변화를 있다고 말하게 됩니다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from src.config import COMPARE_DIFF_THRESHOLD, GAIT_FILTER_VERSION, OVERLAYS_DIR
from src.features import build_features
from src.keypoint_infer import run_keypoint_inference
from src.overlay import render_overlay_video
from src.quality_gate import check_quality
from src.record_store import load_record, save_record
from src.trajectory import build_trajectories


def process_video(video_path, date: str | None = None, note: str | None = None,
                  dog_id: str | None = None) -> dict:
    """영상 하나 → 보행 기록 (이미 저장된 상태로 반환).

    `dog_id` 는 같은 개체의 기록을 묶기 위한 선택 필드입니다. 없어도 동작합니다.
    """
    video_path = Path(video_path)

    records, meta = run_keypoint_inference(video_path)
    quality = check_quality(records)

    record = {
        "record_id": None,
        "source_file": video_path.name,
        # 저장된 원본 파일 경로 (uuid 이름). 화면에 보여줄 이름은 source_file 이고,
        # 이 경로는 원본 재생·향후 삭제 시 지울 대상을 가리키는 용도입니다.
        "original_video": str(video_path),
        "date": date,
        "note": note,
        "dog_id": dog_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "video_meta": {
            "resolution": f"{meta['width']}x{meta['height']}",
            "native_fps": round(meta["native_fps"], 2),
        },
        "quality": quality,
        # 판정 로직의 버전. 다른 버전으로 만들어진 기록끼리 비교하면 같은 영상이라도
        # 수치가 달라 보이므로 compare_records() 가 이 값으로 경고를 붙입니다.
        "gait_filter_version": GAIT_FILTER_VERSION,
    }

    if quality["status"] != "ok":
        record_id = save_record(record)
        record["record_id"] = record_id
        return record

    overlay_path = OVERLAYS_DIR / f"{video_path.stem}_overlay.mp4"
    render_overlay_video(video_path, records, overlay_path)

    trajectories = build_trajectories(records)
    features = build_features(records)

    record.update({
        "overlay_video": str(overlay_path),
        "trajectories": trajectories,
        "features": features,
    })

    record_id = save_record(record)
    record["record_id"] = record_id
    return record


def compare_records(record_id_a: str, record_id_b: str) -> dict:
    """두 기록 비교.

    화면에는 **정성적 서술만** 내려갑니다 ("차이 관찰됨" / "비슷함"). 소수점 수치를 그대로
    노출하면 과신을 부른다고 판단한 결과입니다 — 표본이 작을 때 관절별 비율이 크게
    흩어지는 것을 실측으로 확인했습니다.

    `_dev_only_` 접두어가 붙은 값은 개발·검증용이고 **UI 에 절대 노출하면 안 됩니다.**
    """
    a = load_record(record_id_a)
    b = load_record(record_id_b)

    for r in (a, b):
        if r["quality"]["status"] != "ok":
            return {
                "status": "unavailable",
                "reason": f"비교 불가 — 기록 {r['record_id']}가 분석 가능 상태가 아닙니다.",
                "recommendation": r["quality"].get("recommendation"),
            }

    def _direction_note(va, vb):
        if va is None or vb is None:
            return "비교 불가(한쪽 기록에 없음)"
        denom = max(abs(va), abs(vb), 1e-9)
        rel_diff = abs(va - vb) / denom
        return "차이 관찰됨" if rel_diff > COMPARE_DIFF_THRESHOLD else "비슷함"

    joint_comparison = {}
    sa = a["features"]["summary_for_ui"]
    sb = b["features"]["summary_for_ui"]
    for joint in sorted(set(sa) | set(sb)):
        ja, jb = sa.get(joint), sb.get(joint)
        joint_comparison[joint] = {
            "record_a": ja,
            "record_b": jb,
            "comparison_note": {
                "x": _direction_note(
                    ja.get("x_range") if ja else None, jb.get("x_range") if jb else None
                ),
                "y": _direction_note(
                    ja.get("y_range") if ja else None, jb.get("y_range") if jb else None
                ),
            },
        }

    low_tier_records = [
        r["record_id"] for r in (a, b) if r["quality"].get("quality_tier") != "good"
    ]
    reliability_note = (
        "두 기록 모두 유효 프레임 수가 적어 비교 결과는 참고용입니다."
        if len(low_tier_records) == 2
        else (
            f"기록 {low_tier_records[0]}은(는) 유효 프레임 수가 적어 "
            f"비교 결과가 참고용에 가깝습니다."
            if low_tier_records
            else None
        )
    )

    # 같은 영상이라도 필터 버전이 다르면 포함되는 프레임 구성이 달라져 이동범위가
    # 달라 보입니다 (실측 확인). 그것을 실제 걸음 차이로 읽으면 안 됩니다.
    ver_a = a.get("gait_filter_version")
    ver_b = b.get("gait_filter_version")
    if ver_a != ver_b:
        version_warning = (
            f"두 기록이 서로 다른 분석 버전으로 만들어졌습니다"
            f"(A: {ver_a or '기록 없음(구버전)'} / B: {ver_b or '기록 없음(구버전)'}) — "
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
        if len(xa) > 0
        else None
    )

    return {
        "status": "ok",
        "reason": None,
        "recommendation": None,
        "record_a": {"record_id": a["record_id"], "date": a["date"]},
        "record_b": {"record_id": b["record_id"], "date": b["date"]},
        "message_for_ui": "이전 기록과 비교해 일부 움직임 지표의 차이가 관찰됩니다.",
        "reliability_note": reliability_note,
        "version_warning": version_warning,
        "diff_threshold_note": (
            f"'차이 관찰됨'은 두 값의 상대 차이가 {int(COMPARE_DIFF_THRESHOLD * 100)}% 이상일 때 "
            f"표시됩니다(검증된 임상 기준이 아니라 편의상 잡은 잠정 기준선입니다)."
        ),
        "joint_movement_range_comparison": joint_comparison,
        # ⚠️ 아래 둘은 UI 노출 금지. "보행 건강 점수"처럼 읽히면 안 됩니다.
        "_dev_only_raw_feature_cosine": raw_cosine,
        "_dev_only_n_common_feature_dims": int(mask.sum()),
    }
