"""Real model output evaluation for saved scope; no member writes or database."""

import argparse
import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from daengs_place.place.conversation.saved_search import SavedSearchRequest, plan_saved

from .provider import ObservedGemini
from .runner import DATA, write_json


async def run(args):
    match = re.search(
        r"(?im)^\s*(?:GEMINI_API_KEY\s*=|gemini\s*:)\s*(\S+)",
        args.key_file.read_text(encoding="utf-8-sig"),
    )
    if not match:
        raise ValueError("key file contains no Gemini key")
    model = ObservedGemini(match[1].strip("\"'"), args.model, interval=3)
    cases = json.loads((DATA / "saved-search-cases.json").read_text("utf-8"))
    if args.only:
        wanted = set(args.only.split(","))
        if not wanted <= {c["id"] for c in cases}:
            raise ValueError("unknown case")
        cases = [c for c in cases if c["id"] in wanted]
    directory = DATA / "saved-search-runs" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    directory.mkdir(parents=True)
    write_json(directory / "cases.json", cases)
    root = Path(__file__).resolve().parents[2] / "daengs_place" / "place"
    write_json(
        directory / "metadata.json",
        {
            "model": args.model,
            "boundary": "real Gemini + production compilation; no member API or database",
            "sha256": {
                str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for folder in ("conversation", "providers")
                for p in (root / folder).glob("*.py")
            },
        },
    )
    outcomes = []
    for case in cases:
        request = SavedSearchRequest(
            query=case["query"],
            filters={"lat": 37.5, "lng": 127, "radius_m": 3000, "kinds": ["cafe"]},
        )
        row = {"id": case["id"], "request": request.model_dump(mode="json")}
        start = len(model.calls)
        try:
            intent = await model.plan_saved(request)
            result = plan_saved(request.filters, intent)
            passed = result.action == case["action"]
            if result.filters:
                f = result.filters
                passed &= (
                    f.radius_m == case.get("radius")
                    and f.lat == request.filters.lat
                    and f.lng == request.filters.lng
                    and f.dogs == request.filters.dogs
                )
                if "kinds" in case:
                    passed &= set(f.kinds) == set(case["kinds"])
                parking = case["parking"]
                passed &= f.parking == (parking == "preferred_true")
                atoms = [a for a in f.hard.all if a.capability == "operations.parking"]
                passed &= (
                    (len(atoms) == 1 and atoms[0].value is True)
                    if parking == "required_true"
                    else not atoms
                )
            row.update(
                status="pass" if passed else "fail",
                intent=intent.model_dump(mode="json"),
                result=result.model_dump(mode="json"),
            )
        except Exception as error:  # noqa: BLE001 -- keep provider failures separate without secrets
            row.update(status="incomplete", error_type=type(error).__name__)
        row["provider_calls"] = model.calls[start:]
        outcomes.append(row)
        write_json(directory / "observations.json", outcomes)
        print(f"{case['id']} {row['status']}", flush=True)
    print(directory, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", required=True, type=Path)
    parser.add_argument("--model", default="gemini-3.1-flash-lite")
    parser.add_argument("--only")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
