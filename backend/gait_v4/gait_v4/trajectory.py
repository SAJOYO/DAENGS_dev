# -*- coding: utf-8 -*-
"""관절별 trajectory(프레임별 픽셀 좌표 시퀀스) — walk_demo src/gait_demo/trajectory.build_trajectories 그대로.
gait_usable 프레임만, conf>=min_conf 인 점만. x/y 는 원본 프레임 픽셀(소수 2자리) — overlay 와 그대로 대응."""
from .config import KP_MIN_CONF, MODEL


def build_trajectories(records, joints=None, min_conf=KP_MIN_CONF):
    joints = joints or MODEL["priority"]
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
