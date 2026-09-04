"""업로드 영상을 **원본 그대로 쓸지 변환할지** 판정하는 부분.

**`--extra model` 이 있어야 돕니다** (cv2 · imageio-ffmpeg). 기본 설치에서는 skip 됩니다.

지키는 것:
  · 읽을 수 있는 영상은 **재인코딩하지 않는다** — 손실 변환이 결과를 바꿉니다
  · 읽을 수 없는 영상만 H.264 로 변환한다 (webm/vp9 등 기존 동작 유지)
  · 변환 후에도 못 읽으면 **조용히 넘기지 않고** 예외를 던진다
  · 확장자로 판단하지 않는다 — 확장자는 코덱을 말해주지 않습니다
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

cv2 = pytest.importorskip("cv2", reason="영상 판정에는 --group gait 이 필요합니다")
imageio_ffmpeg = pytest.importorskip("imageio_ffmpeg")
np = pytest.importorskip("numpy")

from daengs_gait import config, video_intake  # noqa: E402
from daengs_gait.video_intake import (  # noqa: E402
    VideoDecodeError,
    probe_decodable,
    save_upload,
)


def _make_video(path: Path, *, n_frames=40, width=64, height=48, container=None) -> Path:
    """실제로 디코딩되는 작은 영상을 만듭니다. `container` 로 확장자와 무관하게 컨테이너를
    고를 수 있습니다 — 확장자로 판단하지 않는다는 것을 시험하려면 필요합니다."""
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg, "-y", "-loglevel", "error", "-nostats",
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{width}x{height}", "-r", "30", "-i", "-",
    ]
    if container:
        cmd += ["-f", container]
    cmd += ["-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE)
    rng = np.random.default_rng(0)
    for _ in range(n_frames):
        proc.stdin.write(rng.integers(0, 255, (height, width, 3), dtype=np.uint8).tobytes())
    proc.stdin.close()
    err = proc.stderr.read().decode(errors="replace")
    proc.stderr.close()
    assert proc.wait() == 0, f"테스트 영상 생성 실패: {err}"
    return path


@pytest.fixture()
def uploads(tmp_path, monkeypatch):
    d = tmp_path / "uploads"
    monkeypatch.setattr(config, "UPLOADS_DIR", d)
    monkeypatch.setattr(video_intake, "UPLOADS_DIR", d)
    return d


# --------------------------------------------------------------------------
# probe
# --------------------------------------------------------------------------
def test_probe_accepts_readable_video(tmp_path):
    src = _make_video(tmp_path / "ok.mp4")
    r = probe_decodable(src)
    assert r.ok
    assert (r.width, r.height) == (64, 48)
    assert r.native_fps > 0
    assert r.frames_read > 0


def test_probe_rejects_garbage(tmp_path):
    bad = tmp_path / "broken.mp4"
    bad.write_bytes(b"not a video at all")
    r = probe_decodable(bad)
    assert not r.ok
    assert r.reason


def test_probe_checks_more_than_the_first_frame(tmp_path):
    """⚠️ 첫 장만 보면 **중간부터 깨지는 파일**을 놓칩니다.

    그래서 전체 구간에 `PROBE_POINTS` 개를 흩어 확인합니다. 여기서는 그 지점 수만큼
    실제로 읽었는지로 그 동작을 고정합니다.
    """
    src = _make_video(tmp_path / "long.mp4", n_frames=120)
    r = probe_decodable(src)
    assert r.ok
    assert r.frames_read == video_intake.PROBE_POINTS


def test_probe_falls_back_to_sequential_when_seek_is_broken(tmp_path, monkeypatch):
    """seek 이 흔들리는 것을 **코덱 문제로 오판하면 안 됩니다** — 읽을 수 있는 영상을
    재인코딩하게 됩니다. seek 이 실패하면 순차 읽기로 다시 확인합니다."""
    src = _make_video(tmp_path / "seekless.mp4", n_frames=120)

    real_capture = cv2.VideoCapture

    class NoSeek:
        """`set()` 이 아무것도 안 하고 `read()` 만 되는 대역 (seek 이 깨진 컨테이너)."""

        def __init__(self, *a, **kw):
            self._cap = real_capture(*a, **kw)
            self._exhausted = False

        def isOpened(self):
            return self._cap.isOpened()

        def get(self, prop):
            return self._cap.get(prop)

        def set(self, *a, **kw):
            # seek 을 무시합니다. 순차로 계속 읽히다가 끝나면 False 가 납니다.
            self._exhausted = True
            return False

        def read(self):
            if self._exhausted:
                return False, None       # seek 뒤 첫 read 를 실패시킵니다
            return self._cap.read()

        def release(self):
            return self._cap.release()

    monkeypatch.setattr("daengs_gait.video_intake.cv2.VideoCapture", NoSeek)
    r = probe_decodable(src)
    assert r.ok                       # 순차 폴백으로 통과해야 합니다
    assert r.method == "sequential"


# --------------------------------------------------------------------------
# save_upload — 원본 보존이 핵심
# --------------------------------------------------------------------------
def test_readable_upload_is_not_reencoded(uploads, tmp_path):
    """⚠️ **이 테스트가 이 변경의 핵심입니다.**

    예전에는 확장자가 `.mp4` 가 아니면 무조건 재인코딩했고, 그 손실이 결과를
    바꿨습니다 — 같은 IMG_8631.mov 에서 detected 99→95 · usable 3→5 · blur 170→200
    으로 갈렸습니다 (2026-08-31 실측).
    """
    # 컨테이너는 mov, 코덱은 H.264 — cv2 가 잘 읽습니다.
    src = _make_video(tmp_path / "clip.mov", container="mov")
    original = src.read_bytes()

    saved = save_upload(original, "clip.mov")

    assert saved.suffix == ".mov"              # 확장자가 안 바뀜
    assert saved.read_bytes() == original      # 바이트가 그대로 (재인코딩 없음)


def test_undecodable_upload_is_converted(uploads, tmp_path, monkeypatch):
    """읽을 수 없는 입력은 기존처럼 H.264 로 변환합니다 (webm/vp9 등)."""
    src = _make_video(tmp_path / "src.mp4")
    content = src.read_bytes()

    calls = {"n": 0}
    real_probe = video_intake.probe_decodable

    def probe_once_bad(path):
        calls["n"] += 1
        if calls["n"] == 1:
            return video_intake.ProbeResult(False, "코덱 미지원(대역)")
        return real_probe(path)

    monkeypatch.setattr(video_intake, "probe_decodable", probe_once_bad)

    saved = save_upload(content, "recording.webm")
    assert saved.suffix == ".mp4"              # 변환됨
    assert calls["n"] == 2                     # 변환 뒤 **다시** 확인함


def test_conversion_failure_raises_instead_of_passing_through(uploads, monkeypatch):
    """⚠️ 변환 후에도 못 읽으면 **조용히 넘기면 안 됩니다.**

    넘기면 분석이 `sampled: 0` 으로 끝나고 사용자는 "밝은 환경에서 다시 촬영해 주세요"
    라는 **엉뚱한 안내**를 받습니다 — 코덱을 못 읽은 것인데 촬영을 탓하게 됩니다.
    """
    monkeypatch.setattr(
        video_intake, "probe_decodable",
        lambda p: video_intake.ProbeResult(False, "언제나 실패(대역)"),
    )
    monkeypatch.setattr(video_intake, "transcode_to_h264", lambda p: p)

    with pytest.raises(VideoDecodeError):
        save_upload(b"whatever", "clip.av1.mp4")


def test_extension_does_not_decide(uploads, tmp_path, monkeypatch):
    """`.mp4` 라는 이름만으로 통과시키지 않습니다 — AV1 이 정확히 그 경우였습니다
    (컨테이너는 mp4, 디코더가 없어 한 장도 못 읽음)."""
    seen = {"probed": 0}
    real_probe = video_intake.probe_decodable

    def counting(path):
        seen["probed"] += 1
        return real_probe(path)

    monkeypatch.setattr(video_intake, "probe_decodable", counting)

    src = _make_video(tmp_path / "x.mp4")
    save_upload(src.read_bytes(), "x.mp4")
    assert seen["probed"] >= 1                 # .mp4 여도 프로브를 거침


def test_ffmpeg_failure_becomes_videodecodeerror_without_leaking_paths(uploads, tmp_path):
    """⚠️ ffmpeg 가 실패할 때 `CalledProcessError` 를 그대로 올리면 안 됩니다.

    `service.py` 가 그것을 `except Exception` 으로 받아 detail 에 문자열화하는데, 그 안에
    **ffmpeg 명령줄 전체와 서버 내부 경로**가 들어갑니다 (실측 614자). 앱에 나가면 안 되는
    정보이고 사용자에게도 아무 도움이 안 됩니다.

    재현: **비디오 스트림이 없는 mp4**(오디오 전용). 프로브가 `0x0` 으로 잡고, ffmpeg 도
    변환할 비디오가 없어 실패합니다.
    """
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    audio_only = tmp_path / "audio_only.mp4"
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=1", "-c:a", "aac", str(audio_only)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    with pytest.raises(VideoDecodeError) as caught:
        save_upload(audio_only.read_bytes(), "audio_only.mp4")

    msg = str(caught.value)
    assert "지원되지 않는 코덱" in msg
    # 사용자 메시지에 내부 정보가 없어야 합니다.
    for leak in ("ffmpeg", "Command", "site-packages", "\\", "/", "libx264"):
        assert leak not in msg, f"메시지에 {leak!r} 가 노출됩니다: {msg}"
    # 원인은 체인에 남아 서버 로그에서 추적됩니다.
    assert isinstance(caught.value.__cause__, subprocess.CalledProcessError)
