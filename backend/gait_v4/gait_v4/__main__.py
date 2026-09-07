# -*- coding: utf-8 -*-
"""CLI: python -m gait_v4 analyze <video> [--follow-cam] [--overlay out.mp4] [--out record.json]
     python -m gait_v4 compare a.json b.json"""
import argparse
import json
import sys


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gait_v4")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze"); a.add_argument("video"); a.add_argument("--follow-cam", action="store_true")
    a.add_argument("--overlay"); a.add_argument("--out"); a.add_argument("--frames", help="프레임별 record 를 저장할 JSON 경로(선택)")
    c = sub.add_parser("compare"); c.add_argument("a"); c.add_argument("b")
    args = ap.parse_args(argv)
    if args.cmd == "analyze":
        from .analyze import analyze_video
        rec, frames = analyze_video(args.video, follow_cam=args.follow_cam, overlay_out=args.overlay)
        txt = json.dumps(rec, ensure_ascii=False, indent=1)
        if args.out:
            open(args.out, "w", encoding="utf-8").write(txt)
        if args.frames:
            json.dump(frames, open(args.frames, "w", encoding="utf-8"), ensure_ascii=False)
        q = rec["quality"]
        print(f"status={q['status']} sampled={q['n_frames_sampled']} detected={q['n_frames_detected']} usable={q['n_frames_gait_usable']} "
              f"tier={q.get('quality_tier')} backend={rec['timing']['ssd_backend']} lr_fix={rec['lr_fix']} "
              f"pose={rec['timing']['pose_elapsed_sec']}s")
        if "features" in rec:
            print("summary_for_ui:", json.dumps(rec["features"]["summary_for_ui"], ensure_ascii=False))
        if not args.out:
            print(txt if len(txt) < 4000 else "(record 가 커서 --out 으로 저장하세요)")
    else:
        from .compare import compare_records
        ra = json.load(open(args.a, encoding="utf-8")); rb = json.load(open(args.b, encoding="utf-8"))
        print(json.dumps(compare_records(ra, rb), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
