"""보행 필터와 feature 엔진의 스모크 테스트.

가중치 없이 도는 것만 담습니다 — 이 서비스는 **가중치가 없는 것이 개발 PC 의 정상 상태**라
(저장소에 없습니다) 모델을 올려야 하는 테스트는 `-m weights` 로 갈라 둡니다.

여기서 지키는 것은 "알고리즘이 walk_demo 에서 옮겨 오면서 안 바뀌었나" 입니다.
임계값 상수 자체를 박아 두는 이유가 그것입니다 — 값이 바뀌면 테스트가 먼저 알려 줍니다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import config  # noqa: E402
from src.feature_engine import build_tracks, static_features_from_records  # noqa: E402
from src.gait_filter import apply_gait_filter, split_interleaved_usable  # noqa: E402
from src.quality_gate import check_quality  # noqa: E402


def _frame(fidx, *, detected=True, n_conf=12, bbox_frac=0.2, center=(100.0, 100.0),
           spread=400.0):
    """테스트용 프레임 레코드 하나.

    keypoint 는 `spread` 픽셀만큼 벌려 배치합니다 — 뭉침 판정(kp_spread_ratio)을
    통과시키려면 실제로 퍼져 있어야 합니다.
    """
    kps = None
    if detected:
        kps = []
        for i, name in enumerate(config.KEYPOINT_NAMES):
            conf = 0.9 if i < n_conf else 0.1
            # 대각선으로 고르게 늘어놓습니다.
            offset = spread * i / max(len(config.KEYPOINT_NAMES) - 1, 1)
            kps.append((name, center[0] + offset, center[1] + offset, conf))
    return {
        "frame_idx": fidx,
        "detected": detected,
        "bbox_frac": bbox_frac if detected else None,
        "bbox_center": center if detected else None,
        "kps": kps,
        "n_confident_kp": n_conf if detected else 0,
        "quality_flags": [],
    }


DIAG = 1000.0


def test_walking_frames_are_usable():
    """프레임마다 충분히 움직이면 전부 보행 가능으로 남아야 합니다."""
    records = [
        _frame(i, center=(100.0 + i * 80.0, 100.0)) for i in range(6)
    ]
    apply_gait_filter(records, DIAG, sample_fps=config.TARGET_FPS)
    assert all(r["gait_usable"] for r in records)


def test_stationary_frames_are_excluded():
    """거의 안 움직이면 정지로 빠져야 합니다 — 앉아 있거나 쓰다듬는 장면."""
    records = [_frame(i, center=(100.0, 100.0)) for i in range(6)]
    apply_gait_filter(records, DIAG, sample_fps=config.TARGET_FPS)
    middle = records[1:-1]  # 양끝은 이웃이 한쪽뿐이라 정지 판정 대상이 아닙니다
    assert all(r["exclude_reason"] == "stationary" for r in middle)


def test_isolated_frame_gets_benefit_of_doubt():
    """이웃이 없는 고립 프레임은 정지로 치지 않습니다 (판단 근거가 없으므로)."""
    records = [_frame(0, center=(100.0, 100.0))]
    apply_gait_filter(records, DIAG, sample_fps=config.TARGET_FPS)
    assert records[0]["gait_usable"] is True


def test_insufficient_keypoints_excluded():
    records = [
        _frame(i, n_conf=config.MIN_CONFIDENT_KP - 1, center=(100.0 + i * 80.0, 100.0))
        for i in range(4)
    ]
    apply_gait_filter(records, DIAG, sample_fps=config.TARGET_FPS)
    assert all(r["exclude_reason"] == "insufficient_keypoints" for r in records)


def test_bad_bbox_size_excluded():
    """너무 멀거나(하한 미만) 클로즈업(상한 초과)이면 빠집니다."""
    too_far = [_frame(i, bbox_frac=0.001, center=(100.0 + i * 80.0, 100.0)) for i in range(3)]
    too_close = [_frame(i, bbox_frac=0.9, center=(100.0 + i * 80.0, 100.0)) for i in range(3)]
    apply_gait_filter(too_far, DIAG, sample_fps=config.TARGET_FPS)
    apply_gait_filter(too_close, DIAG, sample_fps=config.TARGET_FPS)
    assert all(r["exclude_reason"] == "bad_bbox_size" for r in too_far)
    assert all(r["exclude_reason"] == "bad_bbox_size" for r in too_close)


def test_collapsed_keypoints_excluded():
    """confidence 는 높은데 한 군데에 뭉쳐 찍힌 오탐을 걸러야 합니다."""
    records = [
        _frame(i, spread=1.0, center=(100.0 + i * 80.0, 100.0)) for i in range(4)
    ]
    apply_gait_filter(records, DIAG, sample_fps=config.TARGET_FPS)
    assert all(r["exclude_reason"] == "keypoints_collapsed" for r in records)


def test_stationary_judgement_is_fps_independent():
    """같은 실제 움직임이면 샘플링 fps 가 달라도 같은 판정이 나와야 합니다.

    이것이 '거리'가 아니라 '속도'로 비교하도록 바꾼 이유입니다 — 거리로 비교하던 시절에는
    고fps 영상에서 정지 오탐이 급증했습니다.
    """
    # 5fps: 프레임당 0.2초에 80px 이동
    slow = [_frame(i, center=(100.0 + i * 80.0, 100.0)) for i in range(6)]
    apply_gait_filter(slow, DIAG, sample_fps=5.0)
    # 30fps: 프레임당 0.033초라 같은 속도면 이동량이 6분의 1
    fast = [_frame(i, center=(100.0 + i * 80.0 / 6, 100.0)) for i in range(6)]
    apply_gait_filter(fast, DIAG, sample_fps=30.0)

    assert [r["gait_usable"] for r in slow] == [r["gait_usable"] for r in fast]


def test_quality_gate_blocks_when_too_few_frames():
    records = [_frame(i, center=(100.0, 100.0)) for i in range(3)]
    apply_gait_filter(records, DIAG, sample_fps=config.TARGET_FPS)
    quality = check_quality(records)
    assert quality["status"] == "unavailable"
    assert quality["recommendation"]


def test_quality_gate_passes_and_tiers():
    records = [_frame(i, center=(100.0 + i * 80.0, 100.0)) for i in range(10)]
    apply_gait_filter(records, DIAG, sample_fps=config.TARGET_FPS)
    quality = check_quality(records)
    assert quality["status"] == "ok"
    # 10장이면 20 미만이라 low 입니다.
    assert quality["quality_tier"] == "low"


def test_feature_vector_only_keeps_position_keys():
    """`_x_` · `_y_` · `n_obs` 만 남아야 합니다 — disp/vel/acc/autocorr 는 버립니다."""
    records = [_frame(i, center=(100.0 + i * 80.0, 100.0)) for i in range(10)]
    apply_gait_filter(records, DIAG, sample_fps=config.TARGET_FPS)
    usable = [r for r in records if r["gait_usable"]]
    feats = static_features_from_records(usable)

    assert feats, "feature 가 하나도 안 나왔습니다"
    for key in feats:
        assert any(s in key for s in ("_x_", "_y_", "n_obs")), f"버려야 할 키가 남았습니다: {key}"


def test_summary_keys_match_feature_engine_naming():
    """`features._feature_key` 가 만드는 이름이 실제 feature 키와 맞아야 합니다.

    여기가 어긋나면 UI 요약이 조용히 텅 빕니다 — 예외가 안 나서 알아채기 어렵습니다.
    """
    from src.features import _feature_key, build_features

    records = [_frame(i, center=(100.0 + i * 80.0, 100.0)) for i in range(10)]
    apply_gait_filter(records, DIAG, sample_fps=config.TARGET_FPS)
    result = build_features(records)

    assert result["summary_for_ui"], "UI 요약이 비었습니다"
    for joint in result["summary_for_ui"]:
        key = _feature_key(joint)
        assert f"{key}_x_range" in result["internal_feature_vector"]


def test_tracks_are_normalised_within_frame():
    """좌표는 프레임 안에서 [0,1] 로 정규화됩니다 (화면 크기 기준이 아닙니다)."""
    records = [_frame(i, center=(100.0 + i * 80.0, 100.0)) for i in range(4)]
    tracks = build_tracks(records)
    assert tracks
    for track in tracks.values():
        for _, nx, ny in track:
            assert -1e-9 <= nx <= 1 + 1e-9
            assert -1e-9 <= ny <= 1 + 1e-9


def test_split_interleaved_takes_alternating_frames():
    """앞뒤로 자르지 않고 번갈아 나눠야 합니다."""
    records = [_frame(i, center=(100.0 + i * 80.0, 100.0)) for i in range(6)]
    apply_gait_filter(records, DIAG, sample_fps=config.TARGET_FPS)
    a, b = split_interleaved_usable(records)
    assert [r["frame_idx"] for r in a] == [0, 2, 4]
    assert [r["frame_idx"] for r in b] == [1, 3, 5]


def test_thresholds_unchanged_from_walk_demo():
    """walk_demo 실측으로 정해진 값입니다. 바꾸려면 GAIT_FILTER_VERSION 도 함께 올리세요."""
    assert config.TARGET_FPS == 5.0
    assert config.CONF_THRESH == 0.30
    assert config.KP_MIN_CONF == 0.30
    assert config.MIN_CONFIDENT_KP == 6
    assert config.MIN_BBOX_FRAC == 0.03
    assert config.MAX_BBOX_FRAC == 0.65
    assert config.MIN_KP_SPREAD_RATIO == 0.30
    # 원본과 같이 0.02 / 0.2 로 **계산해서** 얻습니다. 부동소수점이라 정확히 0.1 이
    # 아니므로(0.0999...) approx 로 봅니다 — 상수를 직접 0.1 로 적으면 원본과 마지막
    # 비트가 달라지고, 그러면 경계에 걸친 프레임의 판정이 갈릴 수 있습니다.
    assert config.STATIONARY_SPEED_FRAC_PER_SEC == pytest.approx(0.1)
    assert config.STATIONARY_MAX_GAP_SEC == 0.6
    assert config.MIN_USABLE_FRAMES == 4
    assert config.GAIT_FILTER_VERSION == "v5-stationary-speed-based-20260826"
