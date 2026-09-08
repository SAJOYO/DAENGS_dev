# -*- coding: utf-8 -*-
"""프레임별 gait_usable 판정 — walk_demo src/e14_gait_segment_filter.py 의 `_kp_spread_ratio / apply_gait_filter` 그대로.

임계값(config.py)이 바뀌면 GAIT_FILTER_VERSION 을 반드시 올릴 것 — 같은 영상이라도 버전이 다르면 usable 프레임 구성이
달라져 비교 결과가 신뢰 불가능해진다(compare.compare_records 가 이 값으로 경고를 붙인다).
"""
import numpy as np

from .config import (KP_MIN_CONF, MAX_BBOX_FRAC, MIN_BBOX_FRAC, MIN_CONFIDENT_KP, MIN_KP_SPREAD_RATIO,
                     STATIONARY_MAX_GAP_SEC, STATIONARY_SPEED_FRAC_PER_SEC, TARGET_FPS)


def _kp_spread_ratio(r, diag):
    """confident keypoint들이 개 bbox 크기 대비 얼마나 넓게 퍼져 있는지(한 군데에 뭉쳐 찍히면 작다)."""
    conf_pts = [(x, y) for _, x, y, c in (r.get("kps") or []) if c >= KP_MIN_CONF]
    if len(conf_pts) < 2 or not r.get("bbox_frac"):
        return None
    xs = [p[0] for p in conf_pts]
    ys = [p[1] for p in conf_pts]
    kp_span = float(np.hypot(max(xs) - min(xs), max(ys) - min(ys)))
    bbox_scale = diag * float(np.sqrt(r["bbox_frac"]))
    return kp_span / bbox_scale if bbox_scale > 0 else None


def apply_gait_filter(records, diag, min_confident_kp=MIN_CONFIDENT_KP, sample_fps=TARGET_FPS,
                      spread_ratio=MIN_KP_SPREAD_RATIO, stationary_check=True,
                      bbox_frac_range=(MIN_BBOX_FRAC, MAX_BBOX_FRAC)):
    """detected 프레임에 exclude_reason(없으면 gait_usable=True)을 붙인다. 호출할 때마다 덮어쓴다.
    sample_fps: records 가 실제로 샘플링된 fps — "정지" 판정의 인접 프레임 폭을 프레임 수로 환산하는 데 쓴다.
    stationary_check=False: follow-cam(카메라가 개를 따라감) 영상은 bbox 중심이 화면에 고정돼 '정지' 오판 → 건너뜀."""
    max_gap_frames = max(1, round(STATIONARY_MAX_GAP_SEC * sample_fps))
    detected_idx = [i for i, r in enumerate(records) if r["detected"]]

    for i in detected_idx:
        r = records[i]
        if r["n_confident_kp"] < min_confident_kp:
            r["exclude_reason"] = "insufficient_keypoints"
            continue
        lo, hi = bbox_frac_range
        if r["bbox_frac"] is None or not (lo <= r["bbox_frac"] <= hi):
            r["exclude_reason"] = "bad_bbox_size"
            continue
        spread = _kp_spread_ratio(r, diag) if spread_ratio is not None else None
        if spread is not None and spread < spread_ratio:
            r["exclude_reason"] = "keypoints_collapsed"
            continue
        r["exclude_reason"] = None  # 정지 여부는 아래서 이웃 비교로 별도 채움

    still_ok = [i for i in detected_idx if records[i]["exclude_reason"] is None] if stationary_check else []
    for pos, i in enumerate(still_ok):
        r = records[i]
        prev_i = still_ok[pos - 1] if pos > 0 else None
        next_i = still_ok[pos + 1] if pos < len(still_ok) - 1 else None

        def disp_speed(a, b):
            """이웃 프레임 쌍의 이동 '속도'(대각선 비율/초) — 실제 경과 시간으로 나눠 fps 와 무관하게 비교."""
            if a is None or b is None:
                return None
            ra, rb = records[a], records[b]
            gap_frames = abs(rb["frame_idx"] - ra["frame_idx"])
            if gap_frames > max_gap_frames:
                return None
            if ra["bbox_center"] is None or rb["bbox_center"] is None:
                return None
            dx = ra["bbox_center"][0] - rb["bbox_center"][0]
            dy = ra["bbox_center"][1] - rb["bbox_center"][1]
            disp_frac = float(np.hypot(dx, dy) / diag)
            elapsed_sec = gap_frames / sample_fps
            return disp_frac / elapsed_sec if elapsed_sec > 0 else None

        d_prev = disp_speed(i, prev_i)
        d_next = disp_speed(i, next_i)
        both_have_neighbors = d_prev is not None and d_next is not None
        if both_have_neighbors and d_prev < STATIONARY_SPEED_FRAC_PER_SEC and d_next < STATIONARY_SPEED_FRAC_PER_SEC:
            r["exclude_reason"] = "stationary"

    for r in records:
        if not r["detected"]:
            r["gait_usable"] = False
        else:
            r["gait_usable"] = r.get("exclude_reason") is None

    return records
