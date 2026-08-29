"""관절별 trajectory — 프레임별 좌표 시퀀스.

`config.PRIORITY_JOINTS`(슬개골 관련 뒷다리 체인, 해부학적으로 확인된 것)만 뽑습니다.
좌표는 **원본 프레임 픽셀 기준**이라 overlay 영상 위에 그대로 대응시킬 수 있습니다
(feature 쪽의 [0,1] 정규화 좌표와 다릅니다 — 여기는 눈으로 보는 용도입니다).
"""

from __future__ import annotations

from src.config import KP_MIN_CONF, PRIORITY_JOINTS


def build_trajectories(records: list, joints: list | None = None, min_conf: float = KP_MIN_CONF) -> list:
    """반환: [{"joint_name": ..., "frames": [...], "x": [...], "y": [...]}, ...]

    보행 가능(`gait_usable`) 프레임만 포함합니다 — 품질 게이트를 통과 못 한 프레임을
    섞으면 화면에 보이는 궤적과 실제로 분석에 쓴 데이터가 어긋납니다.
    """
    joints = joints or PRIORITY_JOINTS
    out = []
    for joint in joints:
        frames, xs, ys = [], [], []
        for r in records:
            if not r.get("gait_usable") or not r.get("kps"):
                continue
            for name, x, y, conf in r["kps"]:
                if name == joint and conf >= min_conf:
                    frames.append(r["frame_idx"])
                    xs.append(round(float(x), 2))
                    ys.append(round(float(y), 2))
        out.append({"joint_name": joint, "frames": frames, "x": xs, "y": ys})
    return out
