"""Normalize a saved route with DEV's canonical kernel; no API or writer calls."""

import argparse
import json
import uuid
from collections import Counter
from pathlib import Path

from pydantic import Field, model_validator

from .contracts import WalkEvidencePoint
from .diary_input import DiaryContract, Identifier, Instant, digest
from .diary_route_patterns import PATTERN_CASES, normalize_route_patterns
from .evidence import analyze_walk


class RoutePatternSource(DiaryContract):
    session_id: Identifier
    started_at: Instant
    ended_at: Instant
    points: tuple[WalkEvidencePoint, ...] = Field(max_length=20_000)

    @model_validator(mode="after")
    def ordered(self):
        if self.ended_at < self.started_at:
            raise ValueError("session time reversed")
        seqs = [p.client_seq for p in self.points]
        if seqs != sorted(set(seqs)):
            raise ValueError("points need unique increasing sequence")
        return self


def build_patterns(raw, policy=None):
    source = RoutePatternSource.model_validate(raw)
    route = analyze_walk(
        uuid.uuid5(uuid.NAMESPACE_URL, source.session_id),
        source.started_at,
        source.ended_at,
        source.points,
    )
    return normalize_route_patterns(route, digest(source), policy)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--list-cases", action="store_true")
    args = parser.parse_args()
    if args.list_cases:
        if args.input or args.output:
            parser.error("--list-cases takes no input/output")
        print(json.dumps(PATTERN_CASES, ensure_ascii=False, indent=2))
        return
    if not args.input or not args.output:
        parser.error("--input and --output are required")
    raw = json.loads(args.input.read_text(encoding="utf-8-sig"))
    if isinstance(raw["points"], str):
        raw["points"] = json.loads(
            (args.input.parent / raw["points"]).read_text(encoding="utf-8-sig")
        )
    result = build_patterns(raw)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(result.model_dump_json(indent=2) + "\n")
    print(
        json.dumps(
            {
                "cases": dict(Counter(m.case_id for m in result.materials)),
                "quality_rejections": len(result.quality_audit),
            }
        )
    )


if __name__ == "__main__":
    main()
