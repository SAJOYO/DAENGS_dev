"""Live meaning + deterministic candidate effects against synthetic places, no member writes."""

import argparse
import asyncio
import hashlib
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from daengs_place.place.bookmarks import BookmarkFilters
from daengs_place.place.conversation.contract import PrepareRequest
from daengs_place.place.conversation.intent import Interpretation
from daengs_place.place.conversation.render import render_answer
from daengs_place.place.conversation.saved_search import SavedSearchRequest, plan_saved
from daengs_place.place.conversation.service import ConversationService
from daengs_place.place.filters.evaluation import all_true, any_true, evaluate_atoms

from .fixtures import FixtureSearcher, initial_filters
from .provider import ObservedGemini
from .runner import DATA, write_json


def parking_matches(filters, required):
    if filters is None:
        return False
    hard = filters.hard
    outcomes = [
        all_true(
            (
                evaluate_atoms(hard.all, "cafe", parking, None),
                any_true(evaluate_atoms(b.all, "cafe", parking, None) for b in hard.any)
                if hard.any
                else True,
            )
        )
        for parking in (False, True, None)
    ]
    return outcomes == ([False, True, None] if required else [True, True, True])


def assess(case, pool, before, after, receipt):
    expected_pool = pool if case["scope"] == "keep" else case["scope"]
    checks = {
        "pool": after.search_pool == expected_pool,
        "no_write": receipt.bookmark_command is None,
    }
    first = before.snapshot.display_order[0]
    checks["known"] = {p.key for p in after.exploration.known} == (
        {first} if case.get("known") else set()
    )
    checks["excluded"] = {p.key for p in after.exploration.excluded} == (
        {first} if case.get("exclude") else set()
    )
    checks["parking"] = parking_matches(after.filters, case.get("parking", False))
    checks["origin_dogs"] = (
        after.filters.spatial == before.filters.spatial
        and after.filters.dogs == before.filters.dogs
    )
    checks["conditions_preserved"] = set(after.filters.candidate_kinds) == {"cafe"}
    if case.get("clarify"):
        checks["clarify"] = receipt.action == "clarify" and receipt.execution == "not_run"
    if expected_pool == "new_candidates" and case.get("known"):
        checks["not_reintroduced"] = first not in after.snapshot.display_order
    if case.get("browse") == "next":
        checks["next"] = receipt.browse == "next" and not set(after.snapshot.display_order) & set(
            before.snapshot.display_order
        )
    return checks


async def run(args):
    match = re.search(
        r"(?im)^\s*(?:GEMINI_API_KEY\s*=|gemini\s*:)\s*(\S+)",
        args.key_file.read_text(encoding="utf-8-sig"),
    )
    if not match:
        raise ValueError("Gemini key not found")
    model = ObservedGemini(match[1].strip("\"'"), args.model, interval=args.interval)
    cases = json.loads((DATA / "candidate-pool-cases.json").read_text("utf-8"))
    if args.only:
        wanted = set(args.only.split(","))
        if not wanted <= {c["id"] for c in cases}:
            raise ValueError("unknown case")
        cases = [c for c in cases if c["id"] in wanted]
    directory = DATA / "candidate-pool-runs" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    directory.mkdir(parents=True)
    source = Path(__file__).resolve().parents[2] / "daengs_place" / "place"
    write_json(directory / "cases.json", cases)
    write_json(
        directory / "metadata.json",
        {
            "model": args.model,
            "interval_seconds": args.interval,
            "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "boundary": "real interpretation + synthetic search; no member/DB/SQL execution",
            "sha256": {
                str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
                for folder in ("conversation", "providers")
                for p in (source / folder).glob("*.py")
            },
        },
    )
    rows = []
    for case in cases:
        for pool in case["pools"]:
            setup = {
                "candidate_kinds": ["cafe"],
                "origin": {"lat": 37.5, "lng": 127.0},
                "radius_m": 3000,
                "name_query": "",
                "dogs": [],
                "parking": "none",
            }
            fixtures = {
                "defaults": {"source": "kcisa", "address": "합성 주소"},
                "standard": [
                    {
                        "ref": str(i),
                        "name": f"평가 카페 {i}",
                        "kind": "cafe",
                        "parking": (True, False, None)[i % 3],
                        "distance_m": i + 1,
                    }
                    for i in range(46)
                ],
            }
            searcher = FixtureSearcher(setup, fixtures)
            saved = tuple(p.key for p in searcher.rows[:20])
            service = ConversationService(model, searcher=searcher)
            before = (
                await service.prepare(
                    None,
                    PrepareRequest(
                        mode="restore",
                        restore_filters=initial_filters(setup),
                        restore_pool=pool if pool != "bookmarks" else "all_places",
                        candidate_pools="v1",
                        bookmark_keys=saved,
                    ),
                )
            ).state
            start = len(model.calls)
            row = {"id": case["id"], "pool": pool, "query": case["query"]}
            try:
                if pool == "bookmarks":
                    request = SavedSearchRequest(
                        query=case["query"],
                        filters=BookmarkFilters(lat=37.5, lng=127, radius_m=3000, kinds=["cafe"]),
                        search_policy="v1",
                        candidate_pools="v1",
                    )
                    intent = await model.plan_saved(request)
                    plan = plan_saved(
                        request.filters,
                        intent,
                        search_policy="v1",
                        candidate_pools="v1",
                        query=case["query"],
                    )
                    expected = pool if case["scope"] == "keep" else case["scope"]
                    filters = plan.filters or plan.search_filters
                    checks = {
                        "pool": plan.action
                        == ("search" if expected == "bookmarks" else "search_places")
                        and (expected == "bookmarks" or plan.search_pool == expected),
                        "parking": parking_matches(filters, case.get("parking", False)),
                    }
                    row.update(
                        request=request.model_dump(mode="json"), result=plan.model_dump(mode="json")
                    )
                else:
                    request = PrepareRequest(
                        mode="chat",
                        query=case["query"],
                        previous=before,
                        candidate_pools="v1",
                        bookmark_keys=saved,
                        visible_order=before.snapshot.display_order,
                        visible_selected=before.snapshot.display_order[0]
                        if case.get("selected", True)
                        else None,
                    )
                    prepared = await service.prepare(None, request)
                    arguments = [
                        step.get("arguments", {})
                        for call in model.calls[start:]
                        for step in call.get("response", {}).get("steps", [])
                        if step.get("type") == "function_call"
                    ]
                    intent = arguments[-1] if arguments else {}
                    row["raw_intent"] = intent
                    try:
                        intent = Interpretation.model_validate(intent).model_dump(mode="json")
                    except ValueError:
                        row["invalid_interpretation"] = True
                    checks = assess(case, pool, before, prepared.state, prepared.receipt)
                    row.update(
                        request=request.model_dump(mode="json"),
                        result=prepared.model_dump(mode="json"),
                        answer=render_answer(prepared.receipt),
                    )
                raw = intent if isinstance(intent, dict) else intent.model_dump(mode="json")
                meaning = {
                    "scope": raw.get("search_scope") == case["scope"],
                    "save_prohibition": raw.get("forbid_save") == case.get("forbid_save", False),
                    "no_bookmark": raw.get("bookmark") is None,
                }
                if "feedback" in case:
                    meaning["feedback"] = raw.get("feedback") == case["feedback"]
                row.update(
                    intent=raw,
                    meaning_checks=meaning,
                    effect_checks=checks,
                    meaning="pass" if all(meaning.values()) else "fail",
                    effect="pass" if all(checks.values()) else "fail",
                )
            except Exception as error:  # noqa: BLE001 -- no provider secrets in error strings
                row.update(effect="incomplete", error_type=type(error).__name__)
            row["provider_calls"] = model.calls[start:]
            rows.append(row)
            write_json(directory / "observations.json", rows)
            print(
                f"{case['id']}/{pool}: meaning={row.get('meaning')} effect={row['effect']}",
                flush=True,
            )
    print(json.dumps(dict(Counter(r["effect"] for r in rows))), flush=True)
    print(directory, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", required=True, type=Path)
    parser.add_argument("--model", default="gemini-3.1-flash-lite")
    parser.add_argument("--only")
    parser.add_argument("--interval", type=float, default=8)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
