"""Normalize explicitly supplied saved API files. Does not fetch or generate prose."""

import argparse
import json
from pathlib import Path

from .diary_space_cases import CASES
from .diary_space_materials import normalize_spaces


def load_input(path):
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8-sig"))

    def read(value):
        return (
            json.loads((path.parent / value).read_text(encoding="utf-8-sig"))
            if (isinstance(value, str))
            else value
        )

    for kind in ("commerce", "park"):
        if raw.get(kind) is not None:
            raw[kind]["pages"] = [read(p) for p in raw[kind]["pages"]]
    if raw.get("land_cover") is not None:
        raw["land_cover"]["response"] = read(raw["land_cover"]["response"])
    return raw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--list-cases", action="store_true")
    args = parser.parse_args()
    if args.list_cases:
        if args.input or args.output:
            parser.error("--list-cases does not take input/output")
        print(json.dumps(CASES, ensure_ascii=False, indent=2))
        return
    if not args.input or not args.output:
        parser.error("--input and --output are required")
    result = normalize_spaces(load_input(args.input))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(result.model_dump_json(indent=2))
        stream.write("\n")
    print(f"{len(result.materials)} materials; {len(result.audit)} diagnostics; {args.output}")


if __name__ == "__main__":
    main()
