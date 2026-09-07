# -*- coding: utf-8 -*-
"""이식 패키지 검증 — walk_demo 회귀 세트 B층(추적 계층 골든)과 같은 기준으로 gait_v4 를 검사한다.

  1) 환경: DeepLabCut 미설치, torch CPU, 기본 백엔드 direct
  2) 골든 일치: tests/golden_v4_rear.json (walk_demo regress/golden_v4_rear.json 사본, direct 기준 2026-09-07)
     - quality 4키, lr_fix, summary_for_ui(골든에 있는 키), trajectories(프레임 집합 동일 · 최대 차 <= 0.01px), kp_conf, filter version
  3) 재현성: 같은 영상 2회 → 관절 좌표·박스 최대 차 0.0, features 동일
  4) 자기 비교: compare_records(같은 실행 둘) == no_change
실행: python tests/verify.py [영상 경로]   (기본: ../../experiments/animal_pose/videos/rear_IMG8628-1.mp4, follow-cam=on)
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

GOLDEN = HERE / "golden_v4_rear.json"
DEFAULT_VIDEO = HERE.parents[2] / "experiments" / "animal_pose" / "videos" / "rear_IMG8628-1.mp4"
TRAJ_TOL_PX = 0.01


def main():
    video = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_VIDEO
    fails = []

    def check(name, ok, detail=""):
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
        if not ok:
            fails.append(name)

    print("=== 1. 환경")
    try:
        import deeplabcut  # noqa: F401
        check("DeepLabCut 미설치", False, "deeplabcut 이 import 됨 — 이식 env 에 있으면 안 됨")
    except ImportError:
        check("DeepLabCut 미설치", True)
    import torch
    check("torch CPU", not torch.cuda.is_available(), f"torch {torch.__version__} cuda_available={torch.cuda.is_available()}")
    import os
    print(f"  torch threads {torch.get_num_threads()} (GAIT_V4_TORCH_THREADS={os.environ.get('GAIT_V4_TORCH_THREADS')}) — env 값은 검출기 로드 시 적용. 골든은 스레드 1 기준, 다른 값이면 0.01px 반올림 차이가 날 수 있음")
    import gait_v4
    from gait_v4 import analyze_video, compare_records
    from gait_v4.config import GAIT_FILTER_VERSION, MODEL
    print(f"  gait_v4 {gait_v4.__version__} · model {gait_v4.MODEL_ID} · kp_conf {MODEL['kp_conf']} · {GAIT_FILTER_VERSION}")

    print(f"=== 2. 골든 일치 ({video.name})")
    if not video.exists():
        print(f"  영상 없음: {video}"); sys.exit(2)
    t0 = time.perf_counter()
    rec, frames = analyze_video(video, follow_cam=True)
    t1 = time.perf_counter() - t0
    check("backend direct / DLC 미사용", rec["timing"]["ssd_backend"] == "direct" and rec["timing"]["superanimal"] is None, str(rec["timing"]))
    g = json.load(open(GOLDEN, encoding="utf-8"))
    q = {k: rec["quality"].get(k) for k in ("n_frames_sampled", "n_frames_detected", "n_frames_gait_usable", "quality_tier")}
    check("quality", q == g["quality"], f"{q} vs golden {g['quality']}")
    check("lr_fix", rec["lr_fix"] == g["lr_fix"], f"{rec['lr_fix']}")
    check("kp_conf / filter version", rec["pose_model_meta"]["kp_conf"] == g["kp_conf"] and rec["gait_filter_version"] == g["filter"])
    cur_s = rec["features"]["summary_for_ui"]
    s_ok = all(j in cur_s and all(cur_s[j].get(k) == v for k, v in gj.items()) for j, gj in g["summary"].items())
    check("summary_for_ui (골든 키 전부 동일)", s_ok, json.dumps(cur_s, ensure_ascii=False) if not s_ok else "")
    gt = {t["joint_name"]: t for t in g["trajectories"]}
    ct = {t["joint_name"]: t for t in rec["trajectories"]}
    max_d, same_frames = 0.0, True
    for j, t in gt.items():
        c = ct.get(j)
        if not c or c["frames"] != t["frames"]:
            same_frames = False; continue
        for a, b in zip(t["x"] + t["y"], c["x"] + c["y"]):
            max_d = max(max_d, abs(a - b))
    check("trajectories 픽셀 일치", same_frames and max_d <= TRAJ_TOL_PX, f"최대 차 {max_d:.3f}px (tol {TRAJ_TOL_PX}) · 프레임 집합 {'동일' if same_frames else '다름'} · {t1:.0f}s")

    print("=== 3. 재현성 (2회째)")
    rec2, frames2 = analyze_video(video, follow_cam=True)
    d = 0.0
    for a, b in zip(frames, frames2):
        if a["detected"] and b["detected"]:
            for ka, kb in zip(a["kps"], b["kps"]):
                d = max(d, abs(ka[1] - kb[1]), abs(ka[2] - kb[2]), abs(ka[3] - kb[3]))
            d = max(d, max(abs(x - y) for x, y in zip(a["det_box"], b["det_box"])))
    check("관절·박스 최대 차 0.0", d == 0.0, f"{d}")
    check("features 동일", rec["features"]["summary_for_ui"] == rec2["features"]["summary_for_ui"])

    print("=== 4. 자기 비교")
    rec["record_id"], rec2["record_id"] = "run1", "run2"
    cmp = compare_records(rec, rec2)
    check("compare_records no_change", cmp.get("message_kind") == "no_change", f"{cmp.get('message_kind')} {cmp.get('side_summary')}")

    print(f"\n결과: {'PASS' if not fails else 'FAIL ' + str(fails)}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
