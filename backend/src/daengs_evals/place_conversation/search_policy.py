"""Paired live interpretation evaluation for shared search policy, without member/DB actions."""

import argparse
import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from daengs_place.place.bookmarks import BookmarkFilters
from daengs_place.place.conversation.contract import ConversationState, PrepareRequest
from daengs_place.place.conversation.saved_search import SavedSearchRequest, plan_saved
from daengs_place.place.conversation.search_compilation import compile_search, to_search
from daengs_place.place.conversation.search_policy import resolve_search

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
    cases = json.loads((DATA / "search-policy-cases.json").read_text("utf-8"))
    if args.only:
        wanted = set(args.only.split(","))
        if not wanted <= {c["id"] for c in cases}:
            raise ValueError("unknown case")
        cases = [c for c in cases if c["id"] in wanted]
    directory = DATA / "search-policy-runs" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    directory.mkdir(parents=True)
    write_json(directory / "cases.json", cases)
    root = Path(__file__).resolve().parents[2] / "daengs_place" / "place"
    write_json(
        directory / "metadata.json",
        {
            "model": args.model,
            "boundary": "real Gemini + shared policy/compilation; no member API or DB",
            "sha256": {
                str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for folder in ("conversation", "providers")
                for p in (root / folder).glob("*.py")
            },
        },
    )
    outcomes = []
    for case in cases:
        for pool in ("all_places", "bookmarks"):
            before = BookmarkFilters(lat=37.5, lng=127, radius_m=3000, kinds=["cafe"])
            source = before if pool == "bookmarks" else to_search(before)
            request = (
                SavedSearchRequest(query=case["query"], filters=before, search_policy="v1")
                if pool == "bookmarks"
                else PrepareRequest(
                    mode="chat", query=case["query"], previous=ConversationState(filters=source)
                )
            )
            row = {"id": case["id"], "pool": pool, "request": request.model_dump(mode="json")}
            start = len(model.calls)
            try:
                intent = await (
                    model.plan_saved(request) if pool == "bookmarks" else model.plan(request)
                )
                directive = resolve_search(intent, pool, case["query"])
                checks = {
                    "scope": intent.search_scope == case["scope"],
                    "navigation": intent.navigation == case.get("navigation", "stay"),
                    "save_prohibition": intent.forbid_save == case.get("forbid_save", False),
                    "bookmark": (intent.bookmark.operation if intent.bookmark else None)
                    == case.get("bookmark"),
                    "parking": intent.changes.parking == case.get("parking", "keep"),
                    "unresolved": intent.unresolved == case.get("unresolved", "none"),
                }
                if "kinds" in case:
                    checks["kinds"] = (
                        intent.changes.kinds is None
                        and set(before.kinds) == set(case["kinds"])
                        and case.get("kind_operation", "set") == "set"
                    ) or (
                        intent.changes.kinds is not None
                        and set(intent.changes.kinds.values) == set(case["kinds"])
                        and intent.changes.kinds.operation == case.get("kind_operation", "set")
                    )
                compiled = None
                if not directive.question and not directive.navigation and not intent.bookmark:
                    compiled = compile_search(source, intent, directive.pool)
                    actual = (
                        to_search(compiled) if isinstance(compiled, BookmarkFilters) else compiled
                    )
                    expected = to_search(source) if isinstance(source, BookmarkFilters) else source
                    checks["origin_dogs_preserved"] = (
                        actual.dogs == expected.dogs and actual.spatial == expected.spatial
                    )
                result = (
                    plan_saved(source, intent, search_policy="v1", query=case["query"])
                    if pool == "bookmarks"
                    else None
                )
                row.update(
                    status="pass" if all(checks.values()) else "fail",
                    checks=checks,
                    intent=intent.model_dump(mode="json"),
                    resolved_pool=directive.pool,
                    policy_code=directive.code,
                    candidate=compiled.model_dump(mode="json") if compiled else None,
                    saved_plan=result.model_dump(mode="json") if result else None,
                    execution_status="pass"
                    if (
                        (
                            case["scope"] in {"new_candidates", "unbookmarked"}
                            and directive.code == "candidate_pool_unavailable"
                        )
                        or (
                            case["scope"] not in {"new_candidates", "unbookmarked"}
                            and not directive.question
                            and directive.pool
                            == (pool if case["scope"] == "keep" else case["scope"])
                            and directive.navigation
                            == (case.get("navigation", "stay") == "restore_search")
                            and all(v for k, v in checks.items() if k != "scope")
                        )
                    )
                    else "fail",
                )
            except Exception as error:  # noqa: BLE001 -- no secret-bearing provider errors
                row.update(status="incomplete", error_type=type(error).__name__)
            row["provider_calls"] = model.calls[start:]
            outcomes.append(row)
            write_json(directory / "observations.json", outcomes)
            print(f"{case['id']}/{pool}: {row['status']}", flush=True)
    print(directory, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", required=True, type=Path)
    parser.add_argument("--model", default="gemini-3.1-flash-lite")
    parser.add_argument("--only")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
