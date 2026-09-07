# -*- coding: utf-8 -*-
"""feature 생성 — walk_demo 의 e13.build_tracks, e3_trajectory_features._stats/_track_static_temporal,
e14.kp_static_feats_from_recs, gait_demo/features.build_features 를 로직 변경 없이 모았다.

정규화: 프레임마다 conf>=KP_MIN_CONF 인 관절 전체의 x/y 최소·최대로 [0,1] 재정규화(build_tracks).
summary_for_ui 의 x_range/y_range 는 그 정규화 좌표의 max−min(화면·판정이 쓰는 값),
x_range_p90p10/y_range_p90p10 은 같은 좌표의 P90−P10(추가 저장, 판정 미사용 — README §11-17).
알려진 한계: max−min 은 극값 1프레임에 포화될 수 있다(KNOWN_LIMITATIONS §2).
"""
import numpy as np

from .config import FEATURE_VERSION, KP_MIN_CONF, MIN_FRAMES_FOR_PERIODICITY, MODEL, P90P10_MIN_FRAMES


def build_tracks(records):
    """프레임 내 검출점들의 bbox 로 [0,1] 재정규화 → {(name, 0): [(frame_idx, nx, ny), ...]}"""
    tracks = {}
    for rec in records:
        if not rec["detected"]:
            continue
        pts = [(name, x, y) for name, x, y, conf in rec["kps"] if conf >= KP_MIN_CONF]
        if len(pts) < 2:
            continue
        xs = np.array([p[1] for p in pts])
        ys = np.array([p[2] for p in pts])
        if (xs.max() - xs.min()) <= 1e-9 or (ys.max() - ys.min()) <= 1e-9:
            continue
        minx, spanx = xs.min(), xs.max() - xs.min()
        miny, spany = ys.min(), ys.max() - ys.min()
        for name, x, y in pts:
            nx, ny = (x - minx) / spanx, (y - miny) / spany
            tracks.setdefault((name, 0), []).append((rec["frame_idx"], nx, ny))
    return tracks


def _stats(name, values):
    values = np.asarray([v for v in values if v is not None and not np.isnan(v)], dtype=np.float64)
    if len(values) == 0:
        return {f"{name}_mean": np.nan, f"{name}_std": np.nan, f"{name}_min": np.nan,
                f"{name}_max": np.nan, f"{name}_range": np.nan}
    return {
        f"{name}_mean": float(values.mean()),
        f"{name}_std": float(values.std()) if len(values) > 1 else 0.0,
        f"{name}_min": float(values.min()),
        f"{name}_max": float(values.max()),
        f"{name}_range": float(values.max() - values.min()),
    }


def track_feats(track, prefix):
    """(= e3_trajectory_features._track_static_temporal) track: [(frame_idx,x,y), ...] — 정렬 먼저."""
    feats = {}
    track = sorted(track, key=lambda t: t[0])
    xs = [t[1] for t in track]
    ys = [t[2] for t in track]
    idxs = [t[0] for t in track]

    feats.update(_stats(f"{prefix}_x", xs))
    feats.update(_stats(f"{prefix}_y", ys))
    feats[f"{prefix}_n_obs"] = float(len(track))

    if len(track) < 2:
        for k in ("disp", "vel", "acc"):
            feats.update(_stats(f"{prefix}_{k}", []))
        feats[f"{prefix}_autocorr_lag1"] = np.nan
        feats[f"{prefix}_periodicity_valid"] = 0.0
        return feats

    disps, vels = [], []
    for i in range(1, len(track)):
        dt = idxs[i] - idxs[i - 1]
        if dt <= 0:
            continue
        dx = xs[i] - xs[i - 1]
        dy = ys[i] - ys[i - 1]
        d = float(np.hypot(dx, dy))
        disps.append(d)
        vels.append(d / dt)  # frame-index 기준 상대 속도(실제 초 단위 아님)

    feats.update(_stats(f"{prefix}_disp", disps))
    feats.update(_stats(f"{prefix}_vel", vels))

    accs = [vels[i] - vels[i - 1] for i in range(1, len(vels))] if len(vels) > 1 else []
    feats.update(_stats(f"{prefix}_acc", accs))

    if len(ys) >= MIN_FRAMES_FOR_PERIODICITY:
        y = np.asarray(ys) - np.mean(ys)
        denom = float((y ** 2).sum())
        ac1 = float((y[:-1] * y[1:]).sum() / denom) if denom > 1e-9 else np.nan
        feats[f"{prefix}_autocorr_lag1"] = ac1
        feats[f"{prefix}_periodicity_valid"] = 1.0
    else:
        feats[f"{prefix}_autocorr_lag1"] = np.nan
        feats[f"{prefix}_periodicity_valid"] = 0.0

    return feats


def kp_static_feats_from_recs(recs):
    tracks = build_tracks(recs)
    feats = {}
    for (label, inst), track in tracks.items():
        prefix = f"kp_{label.replace(' ', '_').replace('/', '-')}_i{inst}"
        tf = track_feats(track, prefix)
        feats.update({k: v for k, v in tf.items() if any(s in k for s in ("_x_", "_y_", "n_obs"))})
    return feats


def _feature_key(joint: str) -> str:
    return f"kp_{joint.replace(' ', '_').replace('/', '-')}_i0"


def build_features(records, priority_joints=None):
    usable = [r for r in records if r.get("gait_usable")]
    internal_vector = kp_static_feats_from_recs(usable)
    tracks = build_tracks(usable)

    summary = {}
    for joint in (priority_joints or MODEL["priority"]):
        key = _feature_key(joint)
        x_range = internal_vector.get(f"{key}_x_range")
        y_range = internal_vector.get(f"{key}_y_range")
        if x_range is None and y_range is None:
            continue
        px = py = None
        tr = tracks.get((joint, 0))
        if tr and len(tr) >= P90P10_MIN_FRAMES:
            nx = np.array([t[1] for t in tr], dtype=float); ny = np.array([t[2] for t in tr], dtype=float)
            px = float(np.percentile(nx, 90) - np.percentile(nx, 10))
            py = float(np.percentile(ny, 90) - np.percentile(ny, 10))
        summary[joint] = {
            "x_range": round(x_range, 3) if x_range is not None else None,
            "y_range": round(y_range, 3) if y_range is not None else None,
            "x_range_p90p10": round(px, 3) if px is not None else None,
            "y_range_p90p10": round(py, 3) if py is not None else None,
        }

    return {
        "summary_for_ui": summary,                 # UI 노출 가능 (관절별 움직임 범위, [0,1] 스케일)
        "internal_feature_vector": internal_vector,  # 비교(cosine)용 — UI 에 그대로 뿌리지 않는다
        "n_frames_used": len(usable),
        "feature_version": FEATURE_VERSION,
    }
