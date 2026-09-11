"""v4 골든 — `daengs_gait.inference` 가 walk_demo 회귀 골든과 픽셀 단위(0.01px)로 같은가 (D-063 5B).

옛 `backend/gait_v4/tests/verify.py` 의 4항목을 pytest 로 옮긴 것입니다:
  1) 환경: torch CPU · DeepLabCut 미설치 · 기본 백엔드 direct
  2) 골든 일치: `fixtures/gait/golden_v4_rear.json` (walk_demo regress 골든 사본, direct 기준
     2026-09-07) — quality 4키 · lr_fix · summary_for_ui(골든 키 전부) · trajectories(프레임 집합
     동일, 최대 차 ≤ 0.01px) · kp_conf · filter version
  3) 재현성: 같은 영상 2회 → 관절 좌표·박스 최대 차 0.0, features 동일
  4) 자기 비교: `compare_v4.compare_records`(같은 실행 둘) == no_change

**실제 가중치·실제 영상으로만 돕니다.** 둘 중 하나가 없으면 skip 인데 — CI(가중치 없음)에서는
그게 맞지만, **5B 의 로컬 merge 관문에서는 skip 을 통과로 인정하지 않습니다**(계획 05B 재점검
#10). 로컬에서는 반드시 아래 둘을 갖추고 PASS 를 봐야 합니다:

    GAIT_V4_GOLDEN_VIDEO=<IMG_8628_13.mp4 경로>     # sha256 b5e82f46…, 22,558,788 bytes
    GAIT_RELEASE_DIR 에 ssdlite.pt (sha256 6c550a5f…) · rtmpose-m_ap10k/end2end.onnx (1cfd1c86…)

실행은 **서브프로세스**(`python -m daengs_gait.inference analyze --follow-cam …`) 입니다 —
워커가 실제로 쓰는 경로이고, `GAIT_V4_TORCH_THREADS=1` · `CUDA_VISIBLE_DEVICES=-1` 을 자식 env 로
넘겨야 골든 조건(스레드 1 · CPU)이 정확히 재현됩니다(스레드 수가 0.01px 반올림 경계를 가릅니다).
`--follow-cam` 은 verify.py 와 같습니다(골든 영상이 팔로우캠 촬영이라 정지 필터를 끕니다).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "gait"
GOLDEN = FIXTURES / "golden_v4_rear.json"
TRAJ_TOL_PX = 0.01


def _video() -> Path:
    raw = os.environ.get("GAIT_V4_GOLDEN_VIDEO", "")
    if not raw:
        pytest.skip(
            "GAIT_V4_GOLDEN_VIDEO 가 없습니다 — CI 에서는 정상 skip, "
            "로컬 5B 관문에서는 skip 을 통과로 인정하지 않습니다"
        )
    path = Path(raw)
    if not path.exists():
        pytest.skip(f"골든 영상이 없습니다: {path}")
    return path


def _require_weights() -> None:
    from daengs_gait.inference.model import RTMPOSE_ONNX, SSDLITE_WEIGHTS

    missing = [str(p) for p in (SSDLITE_WEIGHTS, RTMPOSE_ONNX) if not p.exists()]
    if missing:
        pytest.skip(f"v4 가중치가 없습니다(GAIT_RELEASE_DIR): {missing}")


def _run_cli(video: Path, out_dir: Path, tag: str) -> tuple[dict, list]:
    out = out_dir / f"{tag}_record.json"
    frames = out_dir / f"{tag}_frames.json"
    env = dict(os.environ, GAIT_V4_TORCH_THREADS="1", CUDA_VISIBLE_DEVICES="-1")
    cmd = [
        sys.executable, "-m", "daengs_gait.inference", "analyze", str(video),
        "--follow-cam", "--out", str(out), "--frames", str(frames),
    ]
    done = subprocess.run(
        cmd, cwd=str(out_dir), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=20 * 60, check=False,
    )
    assert done.returncode == 0, f"v4 CLI 실패 (exit {done.returncode}): {(done.stderr or done.stdout)[-2000:]}"
    return (
        json.loads(out.read_text(encoding="utf-8")),
        json.loads(frames.read_text(encoding="utf-8")),
    )


@pytest.fixture(scope="module")
def two_runs(tmp_path_factory) -> tuple[dict, list, dict, list]:
    pytest.importorskip("cv2")
    pytest.importorskip("torch")
    pytest.importorskip("rtmlib")
    pytest.importorskip("onnxruntime")
    video = _video()
    _require_weights()
    out_dir = tmp_path_factory.mktemp("v4golden")
    rec1, fr1 = _run_cli(video, out_dir, "run1")
    rec2, fr2 = _run_cli(video, out_dir, "run2")
    return rec1, fr1, rec2, fr2


def test_environment_is_cpu_direct_without_deeplabcut(two_runs) -> None:
    """골든 조건은 **자식 프로세스**의 것입니다 — 이 pytest 프로세스에 CUDA 가 보여도(개발 PC 는
    cu126 휠) 자식에는 `CUDA_VISIBLE_DEVICES=-1` 을 넘기므로 거기서 CPU 여야 합니다."""
    rec, *_ = two_runs
    env = dict(os.environ, CUDA_VISIBLE_DEVICES="-1")
    probe = subprocess.run(
        [sys.executable, "-c", "import torch; print(torch.cuda.is_available())"],
        env=env, capture_output=True, text=True, check=False,
    )
    assert probe.stdout.strip() == "False", f"자식 프로세스가 CPU 가 아님: {probe.stdout!r} {probe.stderr[-300:]!r}"
    with pytest.raises(ImportError):
        import deeplabcut  # noqa: F401  — 이식 env 에 있으면 안 됩니다
    assert rec["timing"]["ssd_backend"] == "direct" and rec["timing"]["superanimal"] is None


def test_matches_walk_demo_golden_within_0_01px(two_runs) -> None:
    rec, *_ = two_runs
    g = json.loads(GOLDEN.read_text(encoding="utf-8"))

    q = {k: rec["quality"].get(k) for k in ("n_frames_sampled", "n_frames_detected", "n_frames_gait_usable", "quality_tier")}
    assert q == g["quality"]
    assert rec["lr_fix"] == g["lr_fix"]
    assert rec["pose_model_meta"]["kp_conf"] == g["kp_conf"]
    assert rec["gait_filter_version"] == g["filter"]

    cur_s = rec["features"]["summary_for_ui"]
    for joint, gj in g["summary"].items():
        assert joint in cur_s, joint
        for k, v in gj.items():
            assert cur_s[joint].get(k) == v, f"{joint}.{k}: {cur_s[joint].get(k)} vs 골든 {v}"

    gt = {t["joint_name"]: t for t in g["trajectories"]}
    ct = {t["joint_name"]: t for t in rec["trajectories"]}
    max_d = 0.0
    for j, t in gt.items():
        c = ct.get(j)
        assert c is not None and c["frames"] == t["frames"], f"{j}: 프레임 집합이 다름"
        for a, b in zip(t["x"] + t["y"], c["x"] + c["y"]):
            max_d = max(max_d, abs(a - b))
    assert max_d <= TRAJ_TOL_PX, f"trajectories 최대 차 {max_d:.4f}px > {TRAJ_TOL_PX}"


def test_is_reproducible_run_to_run(two_runs) -> None:
    rec1, fr1, rec2, fr2 = two_runs
    d = 0.0
    for a, b in zip(fr1, fr2):
        if a["detected"] and b["detected"]:
            for ka, kb in zip(a["kps"], b["kps"]):
                d = max(d, abs(ka[1] - kb[1]), abs(ka[2] - kb[2]), abs(ka[3] - kb[3]))
            d = max(d, max(abs(x - y) for x, y in zip(a["det_box"], b["det_box"])))
    assert d == 0.0, f"관절·박스 최대 차 {d}"
    assert rec1["features"]["summary_for_ui"] == rec2["features"]["summary_for_ui"]


def test_self_compare_is_no_change(two_runs) -> None:
    from daengs_gait.compare_v4 import compare_records

    rec1, _, rec2, _ = two_runs
    a = dict(rec1, record_id="run1")
    b = dict(rec2, record_id="run2")
    cmp = compare_records(a, b)
    assert cmp.get("message_kind") == "no_change", f"{cmp.get('message_kind')} {cmp.get('side_summary')}"
    # 골든 영상은 유효 프레임 20(tier ok)이라 "80 미만" 조건 플래그가 붙는 것이 **옛 구현과 같은**
    # 정상 동작입니다 — verify.py 도 message_kind 만 봤습니다. 그 밖의 플래그(follow_cam·해상도·
    # 버전 차이)는 같은 실행 둘이라 없어야 합니다.
    assert all("80" in f for f in cmp["condition_flags"]), cmp["condition_flags"]
