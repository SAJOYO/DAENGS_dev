"""Replay a saved spatial-policy manifest without fetching APIs or calling an LLM."""

import argparse
import json
from collections import Counter
from pathlib import Path

from daengs_walk.diary.slots.memory import replay_spaces
from daengs_walk.diary.space.policy import SpacePolicy


def load_replay(path):
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8-sig"))

    def read(value):
        return (
            json.loads((path.parent / value).read_text(encoding="utf-8-sig"))
            if isinstance(value, str)
            else value
        )

    raw["points"] = read(raw["points"])
    for batch in raw["batches"]:
        batch["materials"] = read(batch["materials"])
    return raw


def summarize(result):
    frames = result.frames
    sources = ("commerce", "park", "land_cover")
    capacity = Counter(d.admission for f in frames for d in f.capacity_stamp.decisions)
    return {
        "input_revision": result.input_revision,
        "frames": len(frames),
        "loaded_frames": {
            s: sum(any(r.source == s for r in f.loaded) for f in frames) for s in sources
        },
        "max_loaded": max(len(f.loaded) for f in frames),
        "capacity_loss_frames": sum(
            any(
                d.admission in {"part_capacity", "total_capacity"}
                for d in f.capacity_stamp.decisions
            )
            for f in frames
        ),
        "capacity_decisions": dict(sorted(capacity.items())),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--park-radius-m", type=float)
    parser.add_argument("--list-policy", action="store_true")
    args = parser.parse_args()
    if args.list_policy:
        if args.input or args.output or args.park_radius_m is None:
            parser.error("--list-policy requires --park-radius-m and no input/output")
        print(
            json.dumps(
                SpacePolicy(park_radius_m=args.park_radius_m).dictionary(),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if not args.input or not args.output:
        parser.error("--input and --output are required")
    raw = load_replay(args.input)
    if args.park_radius_m is not None:
        raw["policy"]["park_radius_m"] = args.park_radius_m
    result = replay_spaces(raw)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(result.model_dump_json(indent=2) + "\n")
    print(json.dumps(summarize(result), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
