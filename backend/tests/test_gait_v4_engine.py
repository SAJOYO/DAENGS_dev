"""보행 분석 엔진 스위치 — v4 = `daengs_gait.engines.subprocess_bridge` (D-063 5B).

v4 는 워커 자신의 인터프리터로 `python -m daengs_gait.inference analyze …` 를 서브프로세스로
부릅니다. 여기서는 **실제로 돌리지 않습니다** — `subprocess.run` 을 가짜로 두고 "무엇을
어떻게 부르고, 결과를 어떻게 읽는지" 만 잡습니다. 진짜 검증은 `test_gait_v4_golden.py`
(walk_demo 골든 0.01px, 실제 가중치) 입니다.

5B 에서 별도 venv(`GAIT_V4_DIR` · `GAIT_V4_PYTHON` · `resolve_dir` · `resolve_python`)가 없어졌고,
명령의 모듈이 `gait_v4` → `daengs_gait.inference` 로 바뀌었습니다. timeout·오류 문구·
record.json 처리는 그대로입니다.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from daengs_backend.services import gait as gait_service
from daengs_gait.engines import subprocess_bridge as bridge

V4_RECORD = {
    "record_id": None,
    "pose_model": "rtmpose_ap10k_ssd",
    "follow_cam": False,
    "video_meta": {"resolution": "1080x1920", "native_fps": 30.0},
    "quality": {"status": "ok", "quality_tier": "good", "n_frames_gait_usable": 120},
    "gait_filter_version": "v5-stationary-speed-based-20260826",
    "features": {
        "summary_for_ui": {
            "L_Hip": {"x_range": 0.10, "y_range": 0.20},
            "R_Hip": {"x_range": 0.11, "y_range": 0.21},
        },
        "internal_feature_vector": {"L_Hip_x": 0.1, "R_Hip_x": 0.11},
        "feature_version": "v2-p90p10-added-20260904",
    },
}


def test_default_engine_is_legacy() -> None:
    """운영에서 아무것도 안 하면 기존 엔진입니다 — 라이선스 결정 전 v4 가 켜지면 안 됩니다."""
    from daengs_backend.config import Settings

    assert Settings.model_fields["gait_engine"].default == "legacy"


def test_v4_settings_are_gone() -> None:
    """5B: 별도 venv 설정이 남아 있으면 누군가 다시 그 길을 열려는 것입니다."""
    from daengs_backend.config import Settings

    assert "gait_v4_dir" not in Settings.model_fields
    assert "gait_v4_python" not in Settings.model_fields
    assert not hasattr(bridge, "resolve_dir") and not hasattr(bridge, "resolve_python")


def _fake_v4_run(*, returncode: int = 0, write_overlay: bool = True):
    """`subprocess.run` 대역 — 인자로 받은 --out 경로에 record 를 쓰고 overlay 를 만듭니다."""
    calls: list[tuple[list[str], dict]] = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        out = Path(cmd[cmd.index("--out") + 1])
        overlay = Path(cmd[cmd.index("--overlay") + 1])
        if returncode == 0:
            record = dict(V4_RECORD)
            if write_overlay:
                overlay.write_bytes(b"v4 overlay")
                record["overlay_video"] = str(overlay)
            out.write_text(json.dumps(record), encoding="utf-8")
        return types.SimpleNamespace(returncode=returncode, stdout="", stderr="boom: 모델 없음")

    return run, calls


def test_v4_engine_calls_own_interpreter_and_returns_legacy_shaped_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`_run_analysis` 가 엔진을 몰라도 되게, 반환 키가 legacy 와 같아야 합니다.

    `services._analyze_from_storage` 가 `get_engine` 으로 v4 를 고르는 경로를 끝까지 탑니다 —
    인터프리터가 `sys.executable` 이고, 모듈이 `daengs_gait.inference` 이며, cwd 가 입력 파일의
    임시 디렉터리이고, timeout 이 5B 전과 같은지도 여기서 봅니다.
    """
    from daengs_backend.config import settings
    from daengs_backend.core import storage as storage_module

    source = tmp_path / "source.bin"
    source.write_bytes(b"video")

    class LocalStorage:
        def local_path(self, key):
            return source

    monkeypatch.setattr(settings, "gait_engine", "v4")
    monkeypatch.setattr(storage_module, "get_storage", lambda: LocalStorage())
    # 입력 판정(3단계)은 여기 주제가 아닙니다 — 가짜 바이트라 실제 프로브는 실패합니다.
    monkeypatch.setattr("daengs_gait.intake.prepare_for_analysis", lambda p: p)
    run, calls = _fake_v4_run()
    import subprocess

    monkeypatch.setattr(subprocess, "run", run)

    result = gait_service._analyze_from_storage("original")

    cmd, kwargs = calls[0]
    assert Path(cmd[0]) == Path(sys.executable)
    assert cmd[1:4] == ["-m", "daengs_gait.inference", "analyze"]
    assert "gait_v4" not in " ".join(cmd)
    local = Path(cmd[4])
    assert kwargs["cwd"] == str(local.parent)
    assert kwargs["timeout"] == bridge.V4_TIMEOUT_SECONDS == 20 * 60
    assert kwargs["check"] is False
    assert result["quality"]["status"] == "ok"
    assert result["pose_model"] == "rtmpose_ap10k_ssd"
    assert result["features"]["summary_for_ui"]["L_Hip"]["x_range"] == 0.10
    assert result["gait_filter_version"] == V4_RECORD["gait_filter_version"]
    assert result["video_meta"]["resolution"] == "1080x1920"
    assert result["_overlay_bytes"] == b"v4 overlay"


def test_v4_engine_failure_surfaces_stderr(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """실패 사유가 `failure_reason` 에 남아야 합니다 — 조용히 빈 결과를 주면 안 됩니다."""
    run, _ = _fake_v4_run(returncode=1)
    import subprocess

    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(RuntimeError, match="exit 1.*모델 없음"):
        bridge.SubprocessBridgeEngine().analyze(tmp_path / "input.bin")


def test_v4_engine_missing_record_json_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import subprocess

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: types.SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    with pytest.raises(RuntimeError, match="record.json"):
        bridge.SubprocessBridgeEngine().analyze(tmp_path / "input.bin")


def test_v4_engine_drops_overlay_path_when_file_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """record 가 overlay 를 광고하는데 파일이 없으면 None — 404 를 내는 URL 을 저장하지 않습니다."""
    run, _ = _fake_v4_run(write_overlay=False)
    import subprocess

    monkeypatch.setattr(subprocess, "run", run)

    record = bridge.SubprocessBridgeEngine().analyze(tmp_path / "input.bin")
    assert record.get("overlay_video") is None


def test_v4_compare_uses_daengs_gait_compare_v4_and_strips_dev_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v4↔v4 비교는 `daengs_gait.compare_v4` (5B) — 계약 필드(`message_kind` · `side_summary` ·
    `condition_flags`)가 있고 `_dev_only_*` 가 없어야 합니다. 비교 함수는 `settings.gait_engine`
    이 아니라 `pose_model` 로 고릅니다."""
    pytest.importorskip("numpy")
    from daengs_backend.config import settings
    from daengs_gait import compare_v4

    monkeypatch.setattr(settings, "gait_engine", "legacy")  # 서버 설정은 비교와 무관해야 합니다
    assert gait_service._load_v4_compare() is compare_v4.compare_records

    a = dict(V4_RECORD, record_id="a")
    b = dict(V4_RECORD, record_id="b")
    result = gait_service._run_compare(a, b, pose_model="rtmpose_ap10k_ssd")

    assert result["status"] == "ok"
    assert result["message_kind"] == "no_change"
    assert set(result["side_summary"]) == {"왼쪽", "오른쪽"}
    assert result["condition_flags"] == []
    assert not any(k.startswith("_dev_only_") for k in result)
