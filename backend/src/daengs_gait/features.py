"""보행 feature — 내부 계산용 벡터와 UI 노출용 요약을 **가릅니다.**

`feature_engine.static_features_from_records` 가 만든 100차원 이상의 벡터가 내부용이고,
그중 관절별 이동 범위(x_range / y_range)만 추려 `summary_for_ui` 로 냅니다.

⚠️ **`internal_feature_vector` 를 UI 에 그대로 뿌리면 안 됩니다.** 수백 개의 숫자가
   화면에 나오면 사용자는 그것을 "보행 건강 점수"로 읽습니다. 이 서비스는 진단이 아니라
   같은 개체의 시간에 따른 변화를 관찰하는 기능입니다.
"""

from __future__ import annotations

import numpy as np

from daengs_gait.config import P90P10_MIN_FRAMES, PRIORITY_JOINTS
from daengs_gait.feature_engine import build_tracks, static_features_from_records


def _feature_key(joint: str) -> str:
    """관절 이름 → feature 키 접두사. `feature_engine` 의 규칙과 같아야 합니다."""
    return f"kp_{joint.replace(' ', '_').replace('/', '-')}_i0"


def build_features(
    records: list,
    *,
    priority_joints: list | None = None,
    p90p10: bool = False,
    feature_version: str | None = None,
) -> dict:
    """보행 프레임들 → UI 요약 + 내부 벡터.

    세 인자는 **엔진마다 다른 부분** 입니다 (D-063 5C). 전부 기본값이 legacy 경로의 지금
    동작이라, 인자를 안 주면 출력이 한 글자도 바뀌지 않습니다:

    - `priority_joints` — 요약에 넣을 관절. 기본은 `config.PRIORITY_JOINTS`(legacy 12kp 기준).
    - `p90p10` — 켜면 관절마다 `x_range_p90p10`·`y_range_p90p10`(같은 정규화 좌표의 P90−P10)을
      **더** 냅니다. max−min 은 극값 한 프레임에 포화될 수 있어 v4 가 참고용으로 같이 저장하는
      값이고, **판정에는 쓰지 않습니다.** 관측이 `P90P10_MIN_FRAMES` 미만이면 `None` 입니다.
    - `feature_version` — 주면 그 문자열을 결과에 답니다. legacy 기록에는 이 키가 없습니다.
    """
    usable = [r for r in records if r.get("gait_usable")]
    internal_vector = static_features_from_records(usable)
    # p90p10 은 정규화 좌표 자체가 필요합니다 — 안 쓸 때는 궤적을 만들지 않습니다.
    tracks = build_tracks(usable) if p90p10 else {}

    summary = {}
    for joint in priority_joints or PRIORITY_JOINTS:
        key = _feature_key(joint)
        x_range = internal_vector.get(f"{key}_x_range")
        y_range = internal_vector.get(f"{key}_y_range")
        # 그 관절이 한 번도 안 잡혔으면 키 자체를 만들지 않습니다 —
        # 0 으로 채우면 "움직임이 없었다"로 읽힙니다.
        if x_range is None and y_range is None:
            continue
        entry = {
            "x_range": round(x_range, 3) if x_range is not None else None,
            "y_range": round(y_range, 3) if y_range is not None else None,
        }
        if p90p10:
            px = py = None
            track = tracks.get((joint, 0))
            if track and len(track) >= P90P10_MIN_FRAMES:
                nx = np.array([t[1] for t in track], dtype=float)
                ny = np.array([t[2] for t in track], dtype=float)
                px = float(np.percentile(nx, 90) - np.percentile(nx, 10))
                py = float(np.percentile(ny, 90) - np.percentile(ny, 10))
            entry["x_range_p90p10"] = round(px, 3) if px is not None else None
            entry["y_range_p90p10"] = round(py, 3) if py is not None else None
        summary[joint] = entry

    out = {
        # UI 에 그대로 보여도 되는 값 (bbox 정규화 [0,1] 스케일의 관절별 이동 범위).
        "summary_for_ui": summary,
        # 비교 계산 전용. UI 노출 금지.
        "internal_feature_vector": internal_vector,
        "n_frames_used": len(usable),
    }
    if feature_version is not None:
        out["feature_version"] = feature_version
    return out
