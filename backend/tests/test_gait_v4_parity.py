"""`daengs_gait` 의 계산이 v4 엔진과 **같은 답을 내는가** (D-063 5C).

같은 계산이 두 벌 있었습니다 — `backend/gait_v4/gait_v4/` 와 `backend/src/daengs_gait/`.
5C 에서 daengs 쪽이 두 엔진 모두를 감당하도록 인자를 받게 했고, 이 파일이 **그 결과가 v4 와
같은지**를 못 박습니다. 5B 에서 v4 코드를 지우고 이쪽으로 갈아탈 때, 갈아탄 것이 맞는지
말해 주는 것이 이 테스트입니다.

**모델도 영상도 없이 돕니다.** v4 CLI 가 남긴 프레임 덤프를 입력으로 쓰기 때문입니다:

    python -m gait_v4 analyze <영상> --out record.json --frames frames.json

이것이 성립하는 이유 셋 —
  · `apply_gait_filter` 는 프레임을 **지우지 않고 표시만** 합니다. 그래서 덤프를 다시 먹여
    `exclude_reason`·`gait_usable` 이 같은지 볼 수 있습니다 (필터 자체가 검증 대상).
  · 품질·궤적·특징의 입력이 덤프 안에 다 있고, `diag`·`sample_fps` 는 record 에서 재구성됩니다.
  · 필요한 서드파티가 `numpy` 뿐입니다 — cv2·torch·onnxruntime 없이 **CI 기본 설치에서** 돕니다
    (numpy 는 `shapely` 를 통해 들어옵니다).

overlay 는 여기 없습니다 — 영상 I/O 라 덤프로 검증할 수 없고, 5B 에서 inference 와 함께 옮깁니다.

## 픽스처 출처

| | |
| --- | --- |
| 골든 영상 | `IMG_8628_13.mp4` (22,558,788 B · sha256 `b5e82f46c8eb8d56f1e2603784889d98a0e99682c2b45558378cfb2928299f26`) |
| 생성 코드 | `backend/gait_v4/` — dev `3a86a8c` 시점, 5C 코드 변경 **전** |
| 생성 명령 | `python -m gait_v4 analyze <영상> --out … --frames …` (`--follow-cam` 없음 = 워커 경로) |
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from daengs_gait.features import build_features
from daengs_gait.gait_filter import apply_gait_filter
from daengs_gait.quality_gate import check_quality
from daengs_gait.trajectory import build_trajectories

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "gait"

#: v4 엔진이 `analyze_video` 에서 넘기는 값들 (`backend/gait_v4/gait_v4/config.py` 의 `MODEL`).
#: record 에 안 담기는 것이라 여기 적어 두고, 아래 `test_v4_engine_config_still_matches` 가
#: 실제 `MODEL` 과 대조합니다 — v4 폴더가 남아 있는 동안은 어긋나면 바로 걸립니다.
V4_MIN_CONFIDENT_KP = 6
V4_SPREAD_RATIO = None  # MODEL["spread_check"] = False → 밀집 검사 건너뜀
V4_BBOX_FRAC_RANGE = (0.0, 0.65)


def _load(name: str) -> dict | list:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def record() -> dict:
    return _load("record_v4_rear")


@pytest.fixture(scope="module")
def frames() -> list:
    return _load("frames_v4_rear")


@pytest.fixture(scope="module")
def meta(record) -> dict:
    """v4 의 `run_pose` 가 넘기던 값을 record 에서 되살립니다.

    `diag` 는 해상도에서 정확히 나옵니다. `sample_fps` 는 `native_fps` 가 record 에 소수점
    두 자리로 반올림돼 있어 **원본과 아주 미세하게 다를 수 있습니다** — 정지 판정이 그
    차이만큼 흔들릴 수 있는데, 아래 필터 테스트가 프레임 하나까지 같은지 보므로 실제로
    흔들리면 그 테스트가 먼저 깨집니다.
    """
    from daengs_gait.config import TARGET_FPS

    width, height = (int(v) for v in record["video_meta"]["resolution"].split("x"))
    native_fps = float(record["video_meta"]["native_fps"])
    step = max(1, round(native_fps / TARGET_FPS))
    return {
        "diag": float(math.hypot(width, height)),
        "sample_fps": native_fps / step,
        "priority": list(record["pose_model_meta"]["priority"]),
        "kp_conf": float(record["pose_model_meta"]["kp_conf"]),
    }


def _diff(got, want, path=""):
    """두 값을 재귀로 대조 — NaN 은 NaN 과 같다고 봅니다 (feature 벡터에 들어 있습니다)."""
    if isinstance(want, dict) and isinstance(got, dict):
        for key in sorted(set(want) | set(got)):
            if key not in got:
                yield f"{path}.{key}: 없음"
            elif key not in want:
                yield f"{path}.{key}: 기대에 없는 키"
            else:
                yield from _diff(got[key], want[key], f"{path}.{key}")
    elif isinstance(want, list) and isinstance(got, list):
        if len(want) != len(got):
            yield f"{path}: 길이 {len(got)} vs {len(want)}"
            return
        for i, (g, w) in enumerate(zip(got, want)):
            yield from _diff(g, w, f"{path}[{i}]")
    elif isinstance(want, float) and isinstance(got, (int, float)):
        if math.isnan(want) and math.isnan(got):
            return
        if not math.isclose(got, want, rel_tol=1e-9, abs_tol=1e-12):
            yield f"{path}: {got!r} vs {want!r}"
    elif got != want:
        yield f"{path}: {got!r} vs {want!r}"


def _filtered(frames, meta) -> list:
    """덤프에 든 프레임을 **같은 인자로** 다시 필터에 통과시킵니다."""
    fresh = json.loads(json.dumps(frames))  # 덤프의 플래그를 지우지 않도록 복사본으로
    return apply_gait_filter(
        fresh,
        meta["diag"],
        min_confident_kp=V4_MIN_CONFIDENT_KP,
        sample_fps=meta["sample_fps"],
        spread_ratio=V4_SPREAD_RATIO,
        stationary_check=True,  # 픽스처는 --follow-cam 없이 만들었습니다 (워커 경로)
        bbox_frac_range=V4_BBOX_FRAC_RANGE,
    )


# ── 필터: 프레임 하나까지 같은 판정 ───────────────────────────────────────
def test_filter_reproduces_every_frame_flag(frames, meta):
    """⚠️ **이 테스트가 나머지의 전제입니다.** 유효 프레임 구성이 어긋나면 품질·궤적·특징이
    전부 따라 어긋납니다 — 그때 뒤의 실패를 쫓지 말고 여기부터 보세요."""
    got = _filtered(frames, meta)
    assert len(got) == len(frames)
    mismatched = [
        (
            g["frame_idx"],
            g.get("gait_usable"),
            w.get("gait_usable"),
            g.get("exclude_reason"),
            w.get("exclude_reason"),
        )
        for g, w in zip(got, frames)
        if g.get("gait_usable") != w.get("gait_usable")
        or g.get("exclude_reason") != w.get("exclude_reason")
    ]
    assert not mismatched, f"플래그가 다른 프레임 {len(mismatched)}개: {mismatched[:5]}"


def test_fixture_really_exercises_both_outcomes(frames):
    """픽스처가 통과·제외를 **둘 다** 담고 있어야 위 테스트가 의미를 갖습니다."""
    usable = sum(1 for r in frames if r.get("gait_usable"))
    assert 0 < usable < len(frames)
    assert {r.get("exclude_reason") for r in frames if not r.get("gait_usable")} != {None}


# ── 품질 ────────────────────────────────────────────────────────────────
def test_quality_matches(frames, meta, record):
    got = check_quality(_filtered(frames, meta), low_tier_note=record["quality"]["quality_note"])
    diffs = list(_diff(got, record["quality"]))
    assert not diffs, diffs


def test_quality_note_is_the_only_engine_difference(frames, meta, record):
    """계산·임계값은 두 엔진이 같고 **문구만** 다릅니다 (5C 실측). 기본 문구로 부르면
    그 한 줄만 달라야 합니다 — 다른 것까지 달라지면 임계값이 갈라진 것입니다."""
    got = check_quality(_filtered(frames, meta))
    diffs = list(_diff(got, record["quality"]))
    want = record["quality"]["quality_note"]
    assert diffs == [f".quality_note: {got['quality_note']!r} vs {want!r}"]


# ── 궤적 ────────────────────────────────────────────────────────────────
def test_trajectories_match(frames, meta, record):
    got = build_trajectories(
        _filtered(frames, meta), joints=meta["priority"], min_conf=meta["kp_conf"]
    )
    diffs = list(_diff(got, record["trajectories"]))
    assert not diffs, diffs[:10]


# ── 특징 (p90p10 포함) ──────────────────────────────────────────────────
def test_features_match_including_p90p10(frames, meta, record):
    got = build_features(
        _filtered(frames, meta),
        priority_joints=meta["priority"],
        p90p10=True,
        feature_version=record["features"]["feature_version"],
    )
    diffs = list(_diff(got, record["features"]))
    assert not diffs, diffs[:10]


def test_legacy_call_stays_free_of_v4_only_fields(frames, meta):
    """**legacy 기본 호출은 지금 그대로여야 합니다** (D-063 5C 결정 A).

    p90p10 과 `feature_version` 은 v4 호환 호출에서만 붙습니다 — 기본 호출에 딸려 나오면
    옛 기록과 새 기록의 필드 구성이 달라집니다.
    """
    got = build_features(_filtered(frames, meta))
    assert "feature_version" not in got
    for entry in got["summary_for_ui"].values():
        assert set(entry) == {"x_range", "y_range"}


# ── v4 엔진 설정이 그대로인지 (v4 폴더가 남아 있는 동안) ──────────────────
def test_v4_engine_config_still_matches():
    """위 상수들은 `gait_v4` 의 `MODEL` 에서 온 값입니다 — 그쪽이 바뀌면 이 parity 가 조용히
    엉뚱한 것을 비교하게 되므로 여기서 대조합니다. 5B 에서 그 폴더가 없어지면 skip 됩니다."""
    import sys

    root = Path(__file__).resolve().parent.parent / "gait_v4"
    if not (root / "gait_v4" / "config.py").exists():
        pytest.skip("backend/gait_v4 가 이 체크아웃에 없습니다 (5B 뒤)")
    sys.path.insert(0, str(root))
    try:
        from gait_v4.config import MODEL
    finally:
        sys.path.remove(str(root))

    assert MODEL["min_confident_kp"] == V4_MIN_CONFIDENT_KP
    assert (MIN_KP_SPREAD_RATIO_IF_ON := MODEL["spread_check"]) is False
    assert V4_SPREAD_RATIO is None and MIN_KP_SPREAD_RATIO_IF_ON is False
    assert tuple(MODEL["bbox_frac_range"]) == V4_BBOX_FRAC_RANGE
