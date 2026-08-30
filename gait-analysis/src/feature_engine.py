"""보행 feature 계산 엔진.

원본은 walk_demo 의 실험 스크립트 세 곳에 흩어져 있었습니다:

    e13_external_video_pilot.build_tracks          → build_tracks
    e3_trajectory_features._stats                  → _stats
    e3_trajectory_features._track_static_temporal  → track_features
    e14_gait_segment_filter.kp_static_feats_from_recs → static_features_from_records

**계산은 한 줄도 바꾸지 않았습니다.** 옮긴 이유는 원본 세 파일이 module 최상단에서
`pandas` · `scipy` · `scikit-learn` 과 다른 실험 모듈(`e12_test_retest` 등)을 import 해서,
알고리즘에는 쓰이지도 않는 무거운 의존성을 서비스 이미지로 끌고 오기 때문입니다.
실제로 이 계산에 필요한 것은 `numpy` 하나뿐입니다.

`frame_idx` 기준 상대 변화량을 씁니다 — 원본 라벨의 timestamp 가 세션 안에서 고정값
(= 신뢰 불가)으로 확인됐기 때문입니다. 그래서 여기서 나오는 속도·가속도는 **실제 초 단위가
아닙니다.**
"""

from __future__ import annotations

import numpy as np

from src.config import KP_MIN_CONF, MIN_FRAMES_FOR_PERIODICITY


def _stats(name: str, values) -> dict:
    """값 목록 하나를 mean/std/min/max/range 다섯 개로 요약합니다.

    NaN 과 None 은 버리고 계산합니다. 하나도 안 남으면 다섯 개 전부 NaN 입니다 —
    0 으로 채우지 않는 것이 중요합니다. "관측이 없다"와 "움직임이 0이다"는 다릅니다.
    """
    values = np.asarray(
        [v for v in values if v is not None and not np.isnan(v)], dtype=np.float64
    )
    if len(values) == 0:
        return {
            f"{name}_mean": np.nan,
            f"{name}_std": np.nan,
            f"{name}_min": np.nan,
            f"{name}_max": np.nan,
            f"{name}_range": np.nan,
        }
    return {
        f"{name}_mean": float(values.mean()),
        # 표본이 하나면 std 가 정의되지 않습니다. numpy 는 0 을 주지만 의미가 다르므로
        # 명시적으로 0.0 을 넣어 둡니다 (원본 동작 그대로).
        f"{name}_std": float(values.std()) if len(values) > 1 else 0.0,
        f"{name}_min": float(values.min()),
        f"{name}_max": float(values.max()),
        f"{name}_range": float(values.max() - values.min()),
    }


def build_tracks(records: list) -> dict:
    """프레임별 검출 결과 → 관절별 궤적. 좌표를 **프레임 안에서** [0,1] 로 재정규화합니다.

    정규화 기준이 화면 크기가 아니라 "그 프레임에서 검출된 점들의 bbox" 라는 점이
    핵심입니다. 개가 카메라에서 멀어지거나 가까워져도 값이 흔들리지 않고, crop-assist 로
    확대해서 얻은 좌표와도 그대로 호환됩니다 (이동 + 등방 스케일에 불변이므로).

    반환: {(관절이름, 0): [(frame_idx, nx, ny), ...]}
    """
    tracks: dict = {}
    for rec in records:
        if not rec["detected"]:
            continue
        pts = [(name, x, y) for name, x, y, conf in rec["kps"] if conf >= KP_MIN_CONF]
        # 점이 하나뿐이면 span 이 0 이라 정규화가 불가능합니다.
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


def track_features(track: list, prefix: str) -> dict:
    """관절 하나의 궤적 → feature 사전.

    track: [(frame_idx, x, y), ...] — 정렬돼 있지 않을 수 있어 먼저 정렬합니다.
    """
    feats: dict = {}
    track = sorted(track, key=lambda t: t[0])
    xs = [t[1] for t in track]
    ys = [t[2] for t in track]
    idxs = [t[0] for t in track]

    feats.update(_stats(f"{prefix}_x", xs))
    feats.update(_stats(f"{prefix}_y", ys))
    feats[f"{prefix}_n_obs"] = float(len(track))

    if len(track) < 2:
        # 변화량을 낼 수 없습니다. 키 구성은 유지하되 전부 NaN 으로 둡니다 —
        # 뒤에서 두 기록을 비교할 때 컬럼이 어긋나지 않아야 합니다.
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
        vels.append(d / dt)  # frame-index 기준 상대 속도 (실제 초 단위가 아닙니다)

    feats.update(_stats(f"{prefix}_disp", disps))
    feats.update(_stats(f"{prefix}_vel", vels))

    accs = [vels[i] - vels[i - 1] for i in range(1, len(vels))] if len(vels) > 1 else []
    feats.update(_stats(f"{prefix}_acc", accs))

    # 주기성: y 좌표 시계열의 lag-1 autocorrelation.
    # 프레임이 부족하면 값 대신 NaN + 플래그를 둡니다 — 적은 표본으로 낸 주기성은
    # 숫자가 나오더라도 읽을 값이 아닙니다.
    if len(ys) >= MIN_FRAMES_FOR_PERIODICITY:
        y = np.asarray(ys) - np.mean(ys)
        denom = float((y**2).sum())
        ac1 = float((y[:-1] * y[1:]).sum() / denom) if denom > 1e-9 else np.nan
        feats[f"{prefix}_autocorr_lag1"] = ac1
        feats[f"{prefix}_periodicity_valid"] = 1.0
    else:
        feats[f"{prefix}_autocorr_lag1"] = np.nan
        feats[f"{prefix}_periodicity_valid"] = 0.0

    return feats


def static_features_from_records(records: list) -> dict:
    """보행 프레임들 → 내부용 feature vector (100차원 이상).

    ⚠️ 관절별로 `_x_` · `_y_` · `n_obs` 가 들어간 키만 남깁니다. `disp` · `vel` · `acc` ·
       `autocorr` 는 계산은 하되 버립니다 — 원본이 그렇게 하고 있고, 그 위에서 검증
       수치(§21 등)가 나왔기 때문입니다. 필터를 넓히면 비교 결과가 달라집니다.
    """
    tracks = build_tracks(records)
    feats: dict = {}
    for (label, inst), track in tracks.items():
        prefix = f"kp_{label.replace(' ', '_').replace('/', '-')}_i{inst}"
        tf = track_features(track, prefix)
        feats.update(
            {k: v for k, v in tf.items() if any(s in k for s in ("_x_", "_y_", "n_obs"))}
        )
    return feats
