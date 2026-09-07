"""보행 분석 엔진 스위치 — walk_demo v4 (#304).

v4 는 별도 venv 의 서브프로세스라 여기서는 **실제로 돌리지 않습니다** — `subprocess.run` 을
가짜로 두고 "무엇을 어떻게 부르고, 결과를 어떻게 읽는지" 만 잡습니다. 진짜 검증은
`backend/gait_v4/tests/verify.py` (walk_demo 골든 0.01px) 입니다.
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from daengs_backend.services import gait as gait_service

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


def test_v4_dir_defaults_to_repo_backend_gait_v4(monkeypatch: pytest.MonkeyPatch) -> None:
    from daengs_backend.config import settings

    monkeypatch.setattr(settings, "gait_v4_dir", "")
    root = gait_service._v4_dir()
    assert root.name == "gait_v4" and root.parent.name == "backend"


def _fake_v4_run(tmp_path: Path, *, returncode: int = 0, write_overlay: bool = True):
    """`subprocess.run` 대역 — 인자로 받은 --out 경로에 record 를 쓰고 overlay 를 만듭니다."""
    calls: list[list[str]] = []

    def run(cmd, **kwargs):
        calls.append(cmd)
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


def test_v4_engine_calls_subprocess_and_returns_legacy_shaped_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`_run_analysis` 가 엔진을 몰라도 되게, 반환 키가 legacy 와 같아야 합니다."""
    from daengs_backend.config import settings
    from daengs_backend.core import storage as storage_module

    source = tmp_path / "source.bin"
    source.write_bytes(b"video")
    venv_py = tmp_path / ".venv" / "Scripts" / "python.exe"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_bytes(b"")
    (tmp_path / ".venv" / "bin").mkdir()
    (tmp_path / ".venv" / "bin" / "python").write_bytes(b"")

    class LocalStorage:
        def local_path(self, key):
            return source

    monkeypatch.setattr(settings, "gait_engine", "v4")
    monkeypatch.setattr(settings, "gait_v4_dir", str(tmp_path))
    monkeypatch.setattr(storage_module, "get_storage", lambda: LocalStorage())
    run, calls = _fake_v4_run(tmp_path)
    import subprocess

    monkeypatch.setattr(subprocess, "run", run)

    result = gait_service._analyze_from_storage("original")

    assert calls and calls[0][1:4] == ["-m", "gait_v4", "analyze"]
    assert result["quality"]["status"] == "ok"
    assert result["features"]["summary_for_ui"]["L_Hip"]["x_range"] == 0.10
    assert result["gait_filter_version"] == V4_RECORD["gait_filter_version"]
    assert result["video_meta"]["resolution"] == "1080x1920"
    assert result["_overlay_bytes"] == b"v4 overlay"


def test_v4_engine_failure_surfaces_stderr(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """실패 사유가 `failure_reason` 에 남아야 합니다 — 조용히 빈 결과를 주면 안 됩니다."""
    from daengs_backend.config import settings

    (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
    (tmp_path / ".venv" / "Scripts" / "python.exe").write_bytes(b"")
    (tmp_path / ".venv" / "bin").mkdir()
    (tmp_path / ".venv" / "bin" / "python").write_bytes(b"")
    monkeypatch.setattr(settings, "gait_v4_dir", str(tmp_path))
    run, _ = _fake_v4_run(tmp_path, returncode=1)
    import subprocess

    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(RuntimeError, match="exit 1.*모델 없음"):
        gait_service._analyze_with_v4(tmp_path / "input.bin")


def test_v4_engine_without_venv_says_how_to_fix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from daengs_backend.config import settings

    monkeypatch.setattr(settings, "gait_v4_dir", str(tmp_path))
    with pytest.raises(RuntimeError, match="uv sync"):
        gait_service._analyze_with_v4(tmp_path / "input.bin")


def test_v4_compare_loads_without_package_init_and_strips_dev_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`gait_v4/compare.py` 를 파일로 불러 backend 웹에서도 돕니다 (torch 없이).

    실제 저장소의 파일을 씁니다 — 계약 필드(`message_kind` · `side_summary` ·
    `condition_flags`)가 있고 `_dev_only_*` 가 없어야 합니다.
    """
    pytest.importorskip("numpy")
    from daengs_backend.config import settings

    monkeypatch.setattr(settings, "gait_engine", "v4")
    monkeypatch.setattr(settings, "gait_v4_dir", "")
    if not (gait_service._v4_dir() / "gait_v4" / "compare.py").exists():
        pytest.skip("backend/gait_v4 가 이 체크아웃에 없습니다")

    a = dict(V4_RECORD, record_id="a")
    b = dict(V4_RECORD, record_id="b")
    result = gait_service._run_compare(a, b)

    assert result["status"] == "ok"
    assert result["message_kind"] == "no_change"
    assert set(result["side_summary"]) == {"왼쪽", "오른쪽"}
    assert result["condition_flags"] == []
    assert not any(k.startswith("_dev_only_") for k in result)


def test_v4_compare_never_imports_gait_v4_package(monkeypatch: pytest.MonkeyPatch) -> None:
    """패키지 `__init__` 을 타면 torch·onnxruntime 을 끌고 옵니다 — 그 길로 가면 안 됩니다."""
    pytest.importorskip("numpy")
    import sys

    from daengs_backend.config import settings

    monkeypatch.setattr(settings, "gait_v4_dir", "")
    if not (gait_service._v4_dir() / "gait_v4" / "compare.py").exists():
        pytest.skip("backend/gait_v4 가 이 체크아웃에 없습니다")
    monkeypatch.delitem(sys.modules, "gait_v4", raising=False)

    gait_service._load_v4_compare()

    assert "gait_v4" not in sys.modules
