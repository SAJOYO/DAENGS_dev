"""보행 feature — 내부 계산용 벡터와 UI 노출용 요약을 **가릅니다.**

`feature_engine.static_features_from_records` 가 만든 100차원 이상의 벡터가 내부용이고,
그중 관절별 이동 범위(x_range / y_range)만 추려 `summary_for_ui` 로 냅니다.

⚠️ **`internal_feature_vector` 를 UI 에 그대로 뿌리면 안 됩니다.** 수백 개의 숫자가
   화면에 나오면 사용자는 그것을 "보행 건강 점수"로 읽습니다. 이 서비스는 진단이 아니라
   같은 개체의 시간에 따른 변화를 관찰하는 기능입니다.
"""

from __future__ import annotations

from src.config import PRIORITY_JOINTS
from src.feature_engine import static_features_from_records


def _feature_key(joint: str) -> str:
    """관절 이름 → feature 키 접두사. `feature_engine` 의 규칙과 같아야 합니다."""
    return f"kp_{joint.replace(' ', '_').replace('/', '-')}_i0"


def build_features(records: list) -> dict:
    usable = [r for r in records if r.get("gait_usable")]
    internal_vector = static_features_from_records(usable)

    summary = {}
    for joint in PRIORITY_JOINTS:
        key = _feature_key(joint)
        x_range = internal_vector.get(f"{key}_x_range")
        y_range = internal_vector.get(f"{key}_y_range")
        # 그 관절이 한 번도 안 잡혔으면 키 자체를 만들지 않습니다 —
        # 0 으로 채우면 "움직임이 없었다"로 읽힙니다.
        if x_range is None and y_range is None:
            continue
        summary[joint] = {
            "x_range": round(x_range, 3) if x_range is not None else None,
            "y_range": round(y_range, 3) if y_range is not None else None,
        }

    return {
        # UI 에 그대로 보여도 되는 값 (bbox 정규화 [0,1] 스케일의 관절별 이동 범위).
        "summary_for_ui": summary,
        # 비교 계산 전용. UI 노출 금지.
        "internal_feature_vector": internal_vector,
        "n_frames_used": len(usable),
    }
