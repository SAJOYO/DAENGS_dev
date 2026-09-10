"""보행 프레임 필터 — 어떤 프레임을 "분석에 쓸 수 있는 보행 장면"으로 볼 것인가.

원본: walk_demo 의 `e14_gait_segment_filter.apply_gait_filter` / `_kp_spread_ratio`.
**판정 로직과 임계값은 한 줄도 바꾸지 않았습니다.** 옮긴 이유는 원본 파일이 module
최상단에서 `pandas` 와 다른 실험 모듈을 끌고 오기 때문입니다 (`feature_engine` 과 같은 사정).

이 필터가 있는 이유: 영상을 통째로 5fps 로 샘플링하면 "모델이 검출에 실패한 프레임"과
"애초에 보행 장면이 아닌 프레임"(땅파기 · 정지 · 클로즈업 · 인트로 · 사람만 나옴)이
검출률 숫자 안에 섞입니다. 전부 **수치 기준**으로 걸러내며, 프레임 단위의 주관적인 육안
판단은 들어가지 않습니다.

임계값은 `config.py` 에 모여 있고, 바꾸면 `GAIT_FILTER_VERSION` 도 함께 올려야 합니다.
"""

from __future__ import annotations

import numpy as np

from daengs_gait.config import (
    KP_MIN_CONF,
    MAX_BBOX_FRAC,
    MIN_BBOX_FRAC,
    MIN_CONFIDENT_KP,
    MIN_KP_SPREAD_RATIO,
    STATIONARY_MAX_GAP_SEC,
    STATIONARY_SPEED_FRAC_PER_SEC,
    TARGET_FPS,
)


def kp_spread_ratio(record: dict, diag: float) -> float | None:
    """confident keypoint 들이 개 몸 크기 대비 얼마나 넓게 퍼져 있는가.

    정상 보행 프레임은 관절이 몸 전체를 훑어 값이 크고, 목·하네스 같은 한 군데에 뭉쳐
    찍힌 오탐은 값이 작습니다. confidence 만으로는 이 오탐이 안 걸러집니다 —
    모델이 "확신을 갖고" 엉뚱한 곳에 12개를 몰아 찍기 때문입니다.
    """
    conf_pts = [(x, y) for _, x, y, c in (record.get("kps") or []) if c >= KP_MIN_CONF]
    if len(conf_pts) < 2 or not record.get("bbox_frac"):
        return None
    xs = [p[0] for p in conf_pts]
    ys = [p[1] for p in conf_pts]
    kp_span = float(np.hypot(max(xs) - min(xs), max(ys) - min(ys)))
    bbox_scale = diag * float(np.sqrt(record["bbox_frac"]))
    return kp_span / bbox_scale if bbox_scale > 0 else None


def apply_gait_filter(
    records: list,
    diag: float,
    min_confident_kp: int = MIN_CONFIDENT_KP,
    sample_fps: float = TARGET_FPS,
    spread_ratio: float | None = MIN_KP_SPREAD_RATIO,
    stationary_check: bool = True,
    bbox_frac_range: tuple[float, float] = (MIN_BBOX_FRAC, MAX_BBOX_FRAC),
) -> list:
    """검출된 프레임에 `exclude_reason` 을 붙이고, 없으면 `gait_usable=True` 로 표시합니다.

    `min_confident_kp` 를 인자로 받는 이유는 같은 추론 결과 위에서 임계값 민감도를
    재추론 없이 다시 재 볼 수 있게 하기 위함입니다 (호출할 때마다 덮어씁니다).

    `sample_fps` 는 records 가 실제로 어떤 fps 로 샘플링됐는지입니다. 정지 판정의 이웃
    프레임 폭을 그 fps 에 맞는 프레임 개수로 환산하는 데 씁니다 — **여기를 `TARGET_FPS`
    로 고정하면 안 됩니다.** 실제 샘플링 fps 는 `native_fps / step` 이라 반올림 오차가 있고,
    그 차이가 정지 판정에 그대로 들어갑니다.

    뒤의 셋은 **엔진마다 다른 값** 입니다 (D-063 5C). 기본값은 legacy 경로가 지금 쓰는 것과
    같으므로 인자를 안 주면 동작이 바뀌지 않습니다:

    - `spread_ratio=None` 이면 keypoint 밀집 검사를 **건너뜁니다.**
    - `stationary_check=False` 는 follow-cam(카메라가 개를 따라가는) 영상용입니다 — bbox 중심이
      화면에 고정돼 걷고 있는데도 전부 '정지' 로 오판하기 때문입니다.
    - `bbox_frac_range` 는 화면 대비 개 크기의 허용 구간입니다.
    """
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
        spread = kp_spread_ratio(r, diag) if spread_ratio is not None else None
        if spread is not None and spread < spread_ratio:
            r["exclude_reason"] = "keypoints_collapsed"
            continue
        # 정지 여부는 이웃 프레임과 비교해야 하므로 아래에서 따로 채웁니다.
        r["exclude_reason"] = None

    # 정지 판정 — 앞뒤 이웃 **양쪽 모두** 저속일 때만 정지로 봅니다.
    # 한쪽만 보면 걷다가 잠깐 멈칫하는 순간이 전부 잘려 나갑니다. 이웃이 없는 고립된
    # 검출 프레임은 판단 근거가 없으므로 정지로 치지 않습니다 (benefit of doubt).
    still_ok = (
        [i for i in detected_idx if records[i]["exclude_reason"] is None]
        if stationary_check
        else []
    )
    for pos, i in enumerate(still_ok):
        r = records[i]
        prev_i = still_ok[pos - 1] if pos > 0 else None
        next_i = still_ok[pos + 1] if pos < len(still_ok) - 1 else None

        def disp_speed(a, b):
            """이웃 프레임 쌍의 이동 '속도' (화면 대각선 비율 / 초).

            거리가 아니라 속도인 것이 핵심입니다 — 프레임 간격은 fps 에 따라 달라지므로
            거리를 고정 기준으로 비교하면 고fps 영상에서 정지 오탐이 급증합니다.
            """
            if a is None or b is None:
                return None
            ra, rb = records[a], records[b]
            gap_frames = abs(rb["frame_idx"] - ra["frame_idx"])
            # 너무 멀리 떨어진 이웃은 평균 속도가 순간 이동 여부를 대변하지 못합니다.
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
        if (
            both_have_neighbors
            and d_prev < STATIONARY_SPEED_FRAC_PER_SEC
            and d_next < STATIONARY_SPEED_FRAC_PER_SEC
        ):
            r["exclude_reason"] = "stationary"

    for r in records:
        if not r["detected"]:
            r["gait_usable"] = False
        else:
            r["gait_usable"] = r.get("exclude_reason") is None

    return records


def split_interleaved_usable(records: list) -> tuple[list, list]:
    """보행 프레임을 번갈아 두 벌로 나눕니다 (재현성 검증용 half-split).

    앞뒤로 자르지 않고 번갈아 뽑는 이유는, 영상의 앞부분과 뒷부분은 촬영 조건이 달라서
    "같은 개의 같은 산책"이 아니라 "다른 두 장면"을 비교하게 되기 때문입니다.
    """
    usable = [r for r in records if r.get("gait_usable")]
    return usable[0::2], usable[1::2]
