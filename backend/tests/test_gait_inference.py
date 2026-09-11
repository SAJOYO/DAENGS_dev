"""PR #62 리뷰 지적 ③④⑤ — overlay 인코딩 · 비교 문구.

**`--group gait` 이 있어야 돕니다** (cv2 · imageio-ffmpeg). 기본 설치에서는 통째로
skip 됩니다 — pyproject 의 "기본 = 화면과 계약만" 가름을 지키는 자리입니다.
이벤트 루프 회귀(①)는 모델 없이도 도는 `test_review_fixes.py` 에 있습니다.

D-063 6단계에서 옛 legacy 추론 runtime(`pipeline.py` · `record_store.py`)이 빠지면서
그 모듈을 통해 보던 것들을 **살아 있는 자리로 옮겼습니다** — 비교 문구(③)는 판정을 실제로
계산하는 `compare.compare_loaded_records` 를 직접 부릅니다(단언은 그대로). 파일 저장
경로에 묶여 있던 ②(원본 파일명 영속)는 그 저장소와 함께 사라졌습니다 — 앱이 보는
`source_file` 은 엔진 기록이 아니라 **DB 행**(`gait_records.source_file`)에서 오고, 그 값은
분석 티켓을 발급할 때 backend 가 요청에서 받아 넣습니다(`services/gait.create`).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

cv2 = pytest.importorskip("cv2", reason="overlay 검증에는 --group gait 이 필요합니다")
imageio_ffmpeg = pytest.importorskip(
    "imageio_ffmpeg", reason="overlay 검증에는 --group gait 이 필요합니다"
)
np = pytest.importorskip("numpy")

from daengs_gait.compare import compare_loaded_records
from daengs_gait.overlay import OverlayEncodeError, render_overlay_video


# --------------------------------------------------------------------------
# 공용
# --------------------------------------------------------------------------
def _make_video(path: Path, width: int, height: int, n_frames: int = 6) -> Path:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    proc = subprocess.Popen(
        [
            ffmpeg, "-y", "-loglevel", "error", "-nostats",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{width}x{height}", "-r", "10", "-i", "-",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    rng = np.random.default_rng(0)
    for _ in range(n_frames):
        proc.stdin.write(rng.integers(0, 255, (height, width, 3), dtype=np.uint8).tobytes())
    proc.stdin.close()
    err = proc.stderr.read().decode(errors="replace")
    proc.stderr.close()
    assert proc.wait() == 0, f"테스트 영상 생성 실패: {err}"
    return path


def _dims(path: Path) -> tuple[int, int]:
    cap = cv2.VideoCapture(str(path))
    ret, frame = cap.read()
    cap.release()
    assert ret, f"프레임을 못 읽었습니다: {path}"
    return frame.shape[1], frame.shape[0]


# --------------------------------------------------------------------------
# ④ 세로 영상 — 크기를 CAP_PROP 이 아니라 디코드된 프레임에서 가져오는가
# --------------------------------------------------------------------------
def test_overlay_size_comes_from_decoded_frame_not_cap_prop(tmp_path, monkeypatch):
    """`CAP_PROP` 이 프레임과 다른 값을 줘도 overlay 가 어긋나지 않아야 합니다.

    회전 메타데이터가 붙은 영상(안드로이드 세로 촬영)에서 OpenCV 는 `read()` 에서
    자동으로 세워 주는데 `CAP_PROP` 은 회전 전 컨테이너 값을 그대로 냅니다.
    옛 코드는 그 값을 ffmpeg `-s` 에 넣어 stride 가 깨졌고 **예외는 안 났습니다.**

    실제 자동회전 여부는 OpenCV 빌드마다 달라 재현이 들쭉날쭉하므로, 여기서는
    `CAP_PROP` 이 **거짓말을 하도록 직접 만들어** 그 상황을 결정적으로 재현합니다.
    """
    src = _make_video(tmp_path / "in.mp4", 64, 96)
    real_w, real_h = _dims(src)

    real_capture = cv2.VideoCapture

    class LyingCapture:
        """`read()` 는 진짜, `get()` 의 프레임 크기만 뒤집어 주는 대역."""

        def __init__(self, *a, **kw):
            self._cap = real_capture(*a, **kw)

        def get(self, prop):
            if prop == cv2.CAP_PROP_FRAME_WIDTH:
                return float(real_h)   # 뒤집힌 값 (회전 전 컨테이너 값 흉내)
            if prop == cv2.CAP_PROP_FRAME_HEIGHT:
                return float(real_w)
            return self._cap.get(prop)

        def read(self):
            return self._cap.read()

        def release(self):
            return self._cap.release()

    monkeypatch.setattr("daengs_gait.overlay.cv2.VideoCapture", LyingCapture)

    out = tmp_path / "overlay.mp4"
    render_overlay_video(src, [], out)

    # 결과가 **디코드된 프레임**과 같은 크기여야 합니다. CAP_PROP 을 믿었다면
    # 96x64 로 나오거나 stride 가 깨져 인코딩이 실패합니다.
    assert _dims(out) == (real_w, real_h)


def test_overlay_plain_video_still_works(tmp_path):
    """평범한 가로 영상도 그대로 동작해야 합니다 (회귀 방지)."""
    src = _make_video(tmp_path / "landscape.mp4", 96, 64)
    out = tmp_path / "overlay.mp4"
    render_overlay_video(src, [], out)
    assert _dims(out) == _dims(src)


# --------------------------------------------------------------------------
# ⑤ ffmpeg 종료 코드
# --------------------------------------------------------------------------
def test_overlay_raises_when_ffmpeg_fails(tmp_path):
    """인코딩이 실패하면 경로를 반환하지 않고 예외를 던져야 합니다.

    옛 코드는 `proc.wait()` 반환값을 버리고 경로를 그대로 돌려줘서, 기록이 없는
    overlay 를 있다고 광고했습니다 (사용자는 404, 서버에는 아무 흔적도 없음).
    """
    src = _make_video(tmp_path / "in.mp4", 32, 32)
    # muxer 를 고를 수 없는 확장자 → ffmpeg 가 즉시 실패합니다.
    out = tmp_path / "overlay.no-such-container"

    with pytest.raises(OverlayEncodeError):
        render_overlay_video(src, [], out)
    assert not out.exists() or out.stat().st_size == 0


def test_overlay_raises_when_no_frames(tmp_path):
    """프레임을 하나도 못 읽으면 조용히 성공하지 않아야 합니다."""
    bogus = tmp_path / "empty.mp4"
    bogus.write_bytes(b"not a video")
    with pytest.raises(OverlayEncodeError):
        render_overlay_video(bogus, [], tmp_path / "out.mp4")


# --------------------------------------------------------------------------
# ③ message_for_ui — 판정을 실제로 계산하는 모듈을 직접 부릅니다
# --------------------------------------------------------------------------
def _record(rid: str, joints: dict) -> dict:
    """`compare_loaded_records` 가 읽는 필드만 채운 기록 (파일에서 왔든 DB 에서 왔든 같습니다)."""
    return {
        "record_id": rid,
        "date": "2026-08-29",
        "quality": {"status": "ok", "quality_tier": "good"},
        "gait_filter_version": "v1",
        "features": {
            "summary_for_ui": joints,
            "internal_feature_vector": {"f0": 1.0, "f1": 2.0},
        },
    }


def test_message_says_no_difference_when_all_joints_similar():
    """모든 관절이 '비슷함' 인데 '차이가 관찰됩니다' 가 나가면 안 됩니다."""
    joints = {"hip": {"x_range": 10.0, "y_range": 10.0}}

    msg = compare_loaded_records(
        _record("aaaaaaaa", joints), _record("bbbbbbbb", joints)
    )["message_for_ui"]
    assert "차이가 관찰됩니다" not in msg
    assert "관찰되지 않았습니다" in msg


def test_message_reports_difference_when_a_joint_differs():
    """실제로 차이가 있으면 그대로 말해야 합니다."""
    a = _record("aaaaaaaa", {"hip": {"x_range": 10.0, "y_range": 10.0}})
    b = _record("bbbbbbbb", {"hip": {"x_range": 1000.0, "y_range": 10.0}})

    assert "차이가 관찰됩니다" in compare_loaded_records(a, b)["message_for_ui"]


def test_message_distinguishes_nothing_comparable_from_similar():
    """비교 가능한 관절이 하나도 없는 것을 '비슷함' 으로 뭉치면 안 됩니다.

    데이터가 없는 것을 '변화가 없다' 고 말하게 되는 자리입니다.
    """
    msg = compare_loaded_records(_record("aaaaaaaa", {}), _record("bbbbbbbb", {}))[
        "message_for_ui"
    ]
    assert "말할 수 없습니다" in msg
    assert "관찰되지 않았습니다" not in msg


def test_message_for_ui_has_no_diagnostic_wording():
    """진단·건강점수처럼 읽히는 낱말이 화면 문구에 들어가면 안 됩니다."""
    banned = ["정상", "건강", "이상 없", "호전", "악화", "점수", "진단"]
    cases = [
        ({"hip": {"x_range": 10.0, "y_range": 10.0}},
         {"hip": {"x_range": 10.0, "y_range": 10.0}}),
        ({"hip": {"x_range": 10.0, "y_range": 10.0}},
         {"hip": {"x_range": 999.0, "y_range": 10.0}}),
        ({}, {}),
    ]
    for i, (ja, jb) in enumerate(cases):
        msg = compare_loaded_records(_record(f"a{i}aaaaaa", ja), _record(f"b{i}bbbbbb", jb))[
            "message_for_ui"
        ]
        for word in banned:
            assert word not in msg, f"{msg!r} 에 금지 표현 {word!r}"
