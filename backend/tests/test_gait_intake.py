"""영상 입력 판정 `daengs_gait.intake` 와 워커 경로 (D-063 3단계).

지키는 것 (8e4a255 의 회귀 — `IMG_8631.mov`):
  · 읽을 수 있는 영상은 **원본 그대로** 엔진에 간다 — 변환 호출 0 회
  · 읽을 수 없을 때만 H.264 로 변환하고, **변환본을 다시 확인**한 뒤 넘긴다
  · 변환 후에도 못 읽거나 ffmpeg 가 실패하면 `VideoDecodeError` — 워커는 FAILED + 사유
  · 워커의 임시 디렉터리 밖에 아무것도 남기지 않는다
  · `daengs_gait.intake` 를 import 해도 cv2 · imageio_ffmpeg · numpy 가 딸려 오지 않는다

정책 테스트는 probe/transcode 를 **대역**으로 갈아 끼워 cv2 없는 CI 에서 돕니다. 실제 영상·코덱
테스트만 `importorskip` 뒤에 있습니다 (`test_gait_video_intake.py` 와 같은 규칙).
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from daengs_backend.services import gait as gait_service
from daengs_gait import intake
from daengs_gait.intake import ProbeResult, VideoDecodeError, prepare_for_analysis

OK = ProbeResult(True, None, 64, 48, 30.0, 8, "seek")
BAD = ProbeResult(False, "코덱 미지원(대역)")


# ── 정책 (cv2 없이) ───────────────────────────────────────────────────────
def test_decodable_input_is_returned_untouched_and_never_transcoded(tmp_path, monkeypatch):
    src = tmp_path / "input.bin"
    src.write_bytes(b"pretend this is an H.264 mov")
    before = src.read_bytes()
    calls: list[str] = []
    monkeypatch.setattr(intake, "probe_decodable", lambda p: calls.append(f"probe:{p.name}") or OK)
    monkeypatch.setattr(intake, "transcode_to_h264", lambda p: calls.append("transcode") or p)

    out = prepare_for_analysis(src)

    assert out == src
    assert src.read_bytes() == before  # 바이트 그대로
    assert calls == ["probe:input.bin"]  # 변환 호출 없음, 프로브 한 번


def test_undecodable_input_is_transcoded_then_reprobed(tmp_path, monkeypatch):
    src = tmp_path / "input.bin"
    src.write_bytes(b"vp9 or av1")
    seen: list[str] = []

    def probe(p):
        seen.append(p.name)
        return BAD if p.name == "input.bin" else OK

    def transcode(p):
        out = p.with_suffix(".mp4")
        out.write_bytes(b"h264")
        p.unlink()
        return out

    monkeypatch.setattr(intake, "probe_decodable", probe)
    monkeypatch.setattr(intake, "transcode_to_h264", transcode)

    out = prepare_for_analysis(src)

    assert out == tmp_path / "input.mp4" and out.exists()
    assert not src.exists()  # 원본은 변환이 지움 — 임시 디렉터리 밖에 남는 것 없음
    assert seen == ["input.bin", "input.mp4"]  # 변환본을 **다시** 확인


def test_transcoded_but_still_undecodable_raises(tmp_path, monkeypatch):
    src = tmp_path / "input.bin"
    src.write_bytes(b"x")
    monkeypatch.setattr(intake, "probe_decodable", lambda p: BAD)
    monkeypatch.setattr(intake, "transcode_to_h264", lambda p: p)

    with pytest.raises(VideoDecodeError, match="변환 후에도 실패"):
        prepare_for_analysis(src)


def test_transcode_process_failure_propagates_as_videodecodeerror(tmp_path, monkeypatch):
    src = tmp_path / "input.bin"
    src.write_bytes(b"x")
    monkeypatch.setattr(intake, "probe_decodable", lambda p: BAD)

    def failing(p):
        raise VideoDecodeError("이 영상 형식을 읽을 수 없습니다. 지원되지 않는 코덱이거나 …")

    monkeypatch.setattr(intake, "transcode_to_h264", failing)
    with pytest.raises(VideoDecodeError, match="읽을 수 없습니다"):
        prepare_for_analysis(src)


def test_intake_module_import_stays_light():
    """`daengs_gait.intake` 만 import 하면 cv2 · imageio_ffmpeg · numpy 가 안 올라옵니다.

    별도 인터프리터에서 봅니다 — 같은 프로세스의 `sys.modules` 는 앞선 테스트에 좌우됩니다.
    """
    probe = (
        "import sys; import daengs_gait.intake; "
        "leaked = [m for m in ('cv2', 'imageio_ffmpeg', 'numpy', 'torch') if m in sys.modules]; "
        "print(','.join(leaked)); sys.exit(1 if leaked else 0)"
    )
    done = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, f"딸려 온 것: {done.stdout.strip() or done.stderr.strip()}"


def test_video_intake_reexports_the_same_objects():
    """옛 HTTP 서비스 모듈이 같은 함수를 쓴다 — 정책이 두 벌이 되지 않게."""
    from daengs_gait import video_intake

    assert video_intake.probe_decodable is intake.probe_decodable
    assert video_intake.transcode_to_h264 is intake.transcode_to_h264
    assert video_intake.VideoDecodeError is intake.VideoDecodeError


# ── 워커 경로: 저장소 → 판정 → 엔진 ───────────────────────────────────────
class _Engine:
    name = "fake"

    def __init__(self) -> None:
        self.seen: list[Path] = []

    def analyze(self, local_path):
        self.seen.append(Path(local_path))
        overlay = Path(local_path).parent / "overlay.mp4"
        overlay.write_bytes(b"ov")
        return {
            "pose_model": "rtmpose_ap10k_ssd",
            "quality": {"status": "ok", "quality_tier": "good"},
            "features": {"summary_for_ui": {}, "internal_feature_vector": {}},
            "gait_filter_version": "v5",
            "video_meta": {"resolution": "1x1", "native_fps": 30.0},
            "overlay_video": str(overlay),
        }


def _wire_engine(monkeypatch):
    from daengs_gait import engines

    engine = _Engine()
    monkeypatch.setattr(engines, "get_engine", lambda name, **kw: engine)
    return engine


def test_local_bridge_input_goes_through_prepare_before_engine(tmp_path, monkeypatch):
    from daengs_backend.core import storage as storage_module

    source = tmp_path / "gait-bridge" / "original.mov"
    source.parent.mkdir()
    source.write_bytes(b"mov bytes")

    class LocalStorage:
        def local_path(self, key):
            return source

    monkeypatch.setattr(storage_module, "get_storage", lambda: LocalStorage())
    engine = _wire_engine(monkeypatch)
    order: list[str] = []
    dirs: list[Path] = []

    def prepare(p):
        order.append("prepare")
        dirs.append(p.parent)
        assert p.read_bytes() == b"mov bytes"  # 다운로드 뒤에 판정
        return p

    monkeypatch.setattr(intake, "prepare_for_analysis", prepare)

    result = gait_service._analyze_from_storage("gait/x/original/original.mov")

    assert order == ["prepare"]
    assert engine.seen == [dirs[0] / "input.bin"]  # 판정이 돌려준 경로가 엔진으로
    assert result["_overlay_bytes"] == b"ov"
    assert not dirs[0].exists()  # 임시 디렉터리는 끝나면 사라진다


def test_gcs_input_goes_through_prepare_before_engine(tmp_path, monkeypatch):
    from daengs_backend.core import storage as storage_module

    class GcsLike:  # local_path 없음 → Signed URL 경로
        def download_url(self, key, *, expires_in_seconds):
            return f"https://signed.example/{key}"

    fetched: list[str] = []

    def urlretrieve(url, local):
        fetched.append(url)
        Path(local).write_bytes(b"gcs bytes")

    monkeypatch.setattr(storage_module, "get_storage", lambda: GcsLike())
    monkeypatch.setattr("urllib.request.urlretrieve", urlretrieve)
    engine = _wire_engine(monkeypatch)
    monkeypatch.setattr(intake, "prepare_for_analysis", lambda p: p.with_suffix(".mp4"))

    gait_service._analyze_from_storage("gait/x/original/a.mp4")

    assert fetched == ["https://signed.example/gait/x/original/a.mp4"]
    assert engine.seen[0].name == "input.mp4"  # 변환본 경로가 그대로 엔진에


def test_transcoded_file_lives_in_the_same_temp_dir_and_is_cleaned(tmp_path, monkeypatch):
    from daengs_backend.core import storage as storage_module

    source = tmp_path / "src.webm"
    source.write_bytes(b"vp9")

    class LocalStorage:
        def local_path(self, key):
            return source

    monkeypatch.setattr(storage_module, "get_storage", lambda: LocalStorage())
    engine = _wire_engine(monkeypatch)
    dirs: list[Path] = []

    def prepare(p):
        dirs.append(p.parent)
        out = p.with_suffix(".mp4")
        out.write_bytes(b"h264")
        p.unlink()
        return out

    monkeypatch.setattr(intake, "prepare_for_analysis", prepare)

    gait_service._analyze_from_storage("k")

    assert engine.seen == [dirs[0] / "input.mp4"]
    assert not dirs[0].exists()
    assert source.exists()  # 저장소의 원본은 건드리지 않는다


# ── 실패는 FAILED + 사유, 엔진 미실행, pose_model NULL ───────────────────────
class _Record:
    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.pet_id = uuid.uuid4()
        self.status = "UPLOADED"
        self.deleted_at = None
        self.original_storage_key = "gait/x/original/a.mp4"
        self.overlay_storage_key = None
        self.failure_reason = None
        self.quality_status = self.quality_tier = self.pose_model = None
        self.quality = self.summary_for_ui = self.internal_feature_vector = None
        self.gait_filter_version = self.video_meta = None


class _Session:
    def __init__(self, record):
        self.record = record

    async def execute(self, stmt):
        record = self.record

        class R:
            def scalar_one_or_none(self):
                return record

        return R()

    async def commit(self):
        pass

    async def rollback(self):
        pass


async def test_undecodable_video_ends_as_failed_with_reason_and_no_engine_run(
    monkeypatch, tmp_path
):
    from daengs_backend.core import database
    from daengs_backend.core import storage as storage_module

    record = _Record()
    session = _Session(record)

    @asynccontextmanager
    async def worker_session():
        yield session

    source = tmp_path / "bad.mp4"
    source.write_bytes(b"av1")

    class LocalStorage:
        def local_path(self, key):
            return source

    monkeypatch.setattr(database, "worker_session", worker_session)
    monkeypatch.setattr(storage_module, "get_storage", lambda: LocalStorage())
    engine = _wire_engine(monkeypatch)

    def prepare(p):
        raise VideoDecodeError(
            "이 영상 형식을 읽을 수 없습니다 (변환 후에도 실패: 코덱). 다시 올려 주세요."
        )

    monkeypatch.setattr(intake, "prepare_for_analysis", prepare)

    await gait_service._run_analysis(record.id)

    assert record.status == "FAILED"
    assert "이 영상 형식을 읽을 수 없습니다" in record.failure_reason
    assert engine.seen == []  # 엔진은 돌지 않았다
    assert record.pose_model is None  # 엔진 결과가 없으니 NULL (1단계 규칙)


# ── 실제 영상·코덱 (cv2 있을 때만) ─────────────────────────────────────────
@pytest.fixture()
def real_video():
    pytest.importorskip("cv2", reason="실제 영상 판정에는 --group gait 이 필요합니다")
    imageio_ffmpeg = pytest.importorskip("imageio_ffmpeg")
    np = pytest.importorskip("numpy")

    def make(path: Path, *, container=None, n_frames=40, width=64, height=48) -> Path:
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        cmd = [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-nostats",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s",
            f"{width}x{height}",
            "-r",
            "30",
            "-i",
            "-",
        ]
        if container:
            cmd += ["-f", container]
        cmd += ["-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )
        rng = np.random.default_rng(0)
        for _ in range(n_frames):
            proc.stdin.write(rng.integers(0, 255, (height, width, 3), dtype=np.uint8).tobytes())
        proc.stdin.close()
        err = proc.stderr.read().decode(errors="replace")
        proc.stderr.close()
        assert proc.wait() == 0, f"테스트 영상 생성 실패: {err}"
        return path

    return make


def test_real_decodable_mov_is_preserved_byte_for_byte(real_video, tmp_path, monkeypatch):
    """⚠️ 이 테스트가 3단계의 핵심 회귀입니다 — `IMG_8631.mov` 를 다시 만들지 않기."""
    src = real_video(tmp_path / "input.bin", container="mov")  # 워커처럼 확장자 없는 이름
    before = src.read_bytes()
    monkeypatch.setattr(intake, "transcode_to_h264", lambda p: pytest.fail("변환이 불렸습니다"))

    out = prepare_for_analysis(src)

    assert out == src and src.read_bytes() == before


def test_real_decodable_mp4_is_preserved(real_video, tmp_path, monkeypatch):
    # 출력 이름에 확장자가 없으면 ffmpeg 이 포맷을 못 정하므로 컨테이너를 명시합니다.
    src = real_video(tmp_path / "input.bin", container="mp4")
    before = src.read_bytes()
    monkeypatch.setattr(intake, "transcode_to_h264", lambda p: pytest.fail("변환이 불렸습니다"))

    assert prepare_for_analysis(src) == src and src.read_bytes() == before


def test_real_transcode_accepts_extensionless_input(real_video, tmp_path, monkeypatch):
    """워커 파일은 `input.bin` 이라 확장자가 없다 — ffmpeg 가 내용으로 판별해 변환한다."""
    src = real_video(tmp_path / "input.bin", container="matroska")
    real_probe = intake.probe_decodable
    monkeypatch.setattr(
        intake, "probe_decodable", lambda p: BAD if p.name == "input.bin" else real_probe(p)
    )

    out = prepare_for_analysis(src)

    assert out == tmp_path / "input.mp4" and out.exists() and not src.exists()
    assert real_probe(out).ok


def test_real_ffmpeg_failure_is_a_clean_videodecodeerror(real_video, tmp_path):
    """비디오 스트림이 없는 파일(오디오 전용) — 프로브 실패, ffmpeg 도 실패. 메시지에 경로·명령 없음."""
    imageio_ffmpeg = pytest.importorskip("imageio_ffmpeg")
    audio_only = tmp_path / "input.bin"
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:a",
            "aac",
            "-f",
            "mp4",
            str(audio_only),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    with pytest.raises(VideoDecodeError) as caught:
        prepare_for_analysis(audio_only)

    msg = str(caught.value)
    for leak in ("ffmpeg", "Command", "site-packages", "\\", "/", "libx264"):
        assert leak not in msg, f"메시지에 {leak!r} 가 노출됩니다: {msg}"
