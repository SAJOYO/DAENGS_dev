"""CLI: python -m daengs_gait.inference analyze <video> [--follow-cam] [--overlay out.mp4] [--out record.json]
     python -m daengs_gait.inference compare a.json b.json

옛 `python -m gait_v4` 의 이식입니다(D-063 5B) — 저장소 안에서 `analyze` 는
`daengs_gait.engines.subprocess_bridge` 가 실제로 부르고, `compare` 는 repo 안에서
호출되는 곳이 없지만(문서용) v4 compare 어댑터가 계획대로 유지되므로 그대로 옮겼습니다
— 리팩터링에 기능 삭제를 섞지 않습니다.
"""
import argparse
import json
import sys


def main(argv=None):
    ap = argparse.ArgumentParser(prog="daengs_gait.inference")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze")
    a.add_argument("video")
    a.add_argument("--follow-cam", action="store_true")
    a.add_argument("--overlay")
    a.add_argument("--out")
    a.add_argument("--frames", help="프레임별 record 를 저장할 JSON 경로(선택)")
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    args = ap.parse_args(argv)
    if args.cmd == "analyze":
        from daengs_gait.inference.analyze import analyze_video

        rec, frames = analyze_video(args.video, follow_cam=args.follow_cam, overlay_out=args.overlay)
        txt = json.dumps(rec, ensure_ascii=False, indent=1)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(txt)
        if args.frames:
            with open(args.frames, "w", encoding="utf-8") as f:
                json.dump(frames, f, ensure_ascii=False)
        q = rec["quality"]
        print(
            f"status={q['status']} sampled={q['n_frames_sampled']} detected={q['n_frames_detected']} "
            f"usable={q['n_frames_gait_usable']} tier={q.get('quality_tier')} "
            f"backend={rec['timing']['ssd_backend']} lr_fix={rec['lr_fix']} "
            f"pose={rec['timing']['pose_elapsed_sec']}s"
        )
        if "features" in rec:
            print("summary_for_ui:", json.dumps(rec["features"]["summary_for_ui"], ensure_ascii=False))
        if not args.out:
            print(txt if len(txt) < 4000 else "(record 가 커서 --out 으로 저장하세요)")
    else:
        from daengs_gait.compare_v4 import compare_records

        with open(args.a, encoding="utf-8") as fa, open(args.b, encoding="utf-8") as fb:
            ra, rb = json.load(fa), json.load(fb)
        print(json.dumps(compare_records(ra, rb), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
