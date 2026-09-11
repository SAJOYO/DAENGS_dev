"""overlay parity — 5B 에서 합친 `daengs_gait.overlay._draw_frame` 이 두 엔진 모두에서 옛 결과와
**픽셀 단위로** 같은가 (D-063 5B 재점검 #5).

인코딩(H.264)을 거치지 않습니다 — `_draw_frame` 은 순수 함수라, 같은 캔버스·같은 record·같은
인자로 돌린 numpy 배열의 sha256 을 **옛 구현으로 미리 얼려 둔 값**(`overlay_v4_parity.json`)과
대조합니다.

  · v4 모드: 실제 v4 프레임 69개(`frames_v4_rear.json`, 골든 영상)를 v4 `model_meta` 로 —
    옛 `backend/gait_v4/gait_v4/overlay.py::_draw_frame` 의 해시
  · legacy 기본(인자 없음): 12kp 합성 record 12개 — 5B 이전 `daengs_gait/overlay.py` 의 해시

둘 다 dev `aef5adf0`(5B 구현 전) 시점 구현으로 생성했습니다. cv2 가 필요합니다(`gait` 그룹) —
버전이 lock 에 `==` 로 박혀 있어 렌더 결과가 환경마다 흔들리지 않습니다.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from daengs_gait import overlay
from daengs_gait.inference.model import model_meta

FIXTURES = Path(__file__).parent / "fixtures" / "gait"


def _fixture():
    return json.loads((FIXTURES / "overlay_v4_parity.json").read_text(encoding="utf-8"))


def _canvas(spec):
    return np.full((spec["height"], spec["width"], 3), spec["fill"], dtype=np.uint8)


def _sha(arr) -> str:
    return hashlib.sha256(np.ascontiguousarray(arr).tobytes()).hexdigest()


def test_v4_mode_matches_old_gait_v4_overlay_pixel_for_pixel() -> None:
    fx = _fixture()
    mm = model_meta()
    # 픽스처가 만들어질 때의 model_meta 와 지금 것이 같아야 같은 그림을 그립니다.
    assert mm == fx["v4_model_meta"], "v4 model_meta 가 바뀌었습니다 — 픽스처를 다시 만들 결정이 먼저"
    frames = json.loads((FIXTURES / fx["v4_frames_fixture"]).read_text(encoding="utf-8"))
    edges = [tuple(e) for e in mm["skeleton"]]
    mismatched = []
    for rec in frames:
        img = overlay._draw_frame(
            _canvas(fx["canvas"]), rec, edges=edges, kp_conf=mm["kp_conf"], priority=mm["priority"]
        )
        if _sha(img) != fx["v4_hashes"][str(rec["frame_idx"])]:
            mismatched.append(rec["frame_idx"])
    assert len(frames) == len(fx["v4_hashes"]) == 69
    assert mismatched == [], f"옛 v4 overlay 와 다른 프레임: {mismatched}"


def test_legacy_default_arguments_render_exactly_as_before_5b() -> None:
    """인자를 안 주면 legacy 출력이 한 픽셀도 바뀌지 않습니다 — 5C 의 '기본값 = 지금 legacy 동작'
    규칙을 overlay 에도 그대로 적용한 것입니다."""
    fx = _fixture()
    mismatched = []
    for rec in fx["legacy_records"]:
        if rec["kps"] is not None:
            rec = dict(rec, kps=[tuple(k) for k in rec["kps"]])
        img = overlay._draw_frame(_canvas(fx["canvas"]), rec)
        if _sha(img) != fx["legacy_hashes"][str(rec["frame_idx"])]:
            mismatched.append(rec["frame_idx"])
    assert mismatched == [], f"5B 이전 legacy overlay 와 다른 프레임: {mismatched}"


def test_side_color_is_inert_for_legacy_joint_names() -> None:
    """legacy 12 관절 이름은 전부 None — 좌우 색이 legacy 그림에 새지 않는 근거입니다."""
    from daengs_gait.config import KEYPOINT_NAMES

    assert all(overlay._side_color(n) is None for n in KEYPOINT_NAMES)
    assert overlay._side_color("L_Hip") != overlay._side_color("R_Hip")
    assert overlay._side_color("Neck") is None


def test_render_overlay_video_signature_keeps_model_meta_optional() -> None:
    import inspect

    params = inspect.signature(overlay.render_overlay_video).parameters
    assert params["model_meta"].default is None
