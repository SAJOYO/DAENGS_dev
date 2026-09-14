"""Live evaluation against the repository modules; synthetic data, no operational DB."""

import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from daengs_backend.services.facility_tools.dialogue import FacilityDialogue, ToolTurn
from daengs_evals.facility_tools.playground import new_workspace, read_key
from daengs_place.place.commands.view import ui_view
from daengs_place.place.providers.conversation_gemini import GeminiConversation

EVALUATION_VERSION = 2
QUIET_PROPOSAL = {
    "kinds": ["cafe"],
    "parking": True,
    "unavailable_contains": "조용",
    "apply_to": "results",
}

SCENARIOS = [
    [
        (
            "주차되는 카페만 보여줘",
            {"kinds": ["cafe"], "parking": True, "cards": "A", "tool": "search_places"},
        ),
        (
            "음식점도 추가해줘",
            {
                "kinds": ["cafe", "restaurant"],
                "parking": True,
                "cards": "AE",
                "tool": "search_places",
            },
        ),
        (
            "주차는 상관없어",
            {
                "kinds": ["cafe", "restaurant"],
                "parking": None,
                "cards": "ABCDEF",
                "tool": "search_places",
            },
        ),
        (
            "두 번째는 주차 돼?",
            {
                "unchanged": True,
                "tool": "get_place_details",
                "target": 1,
                "details": {"parking": {"status": "known", "value": False}},
            },
        ),
        ("두 번째 장소 선택해줘", {"selected": "B", "tool": "select_place", "target": 1}),
        (
            "선택한 곳은 제외해줘",
            {
                "cards": "ACDEF",
                "excluded": 1,
                "tool": "set_place_excluded",
                "target": "selected",
            },
        ),
        (
            "다른 곳 보여줘",
            {
                "unchanged": True,
                "cards": "ACDEF",
                "tool": "next_places",
                "result": {
                    "code": "no_more_candidates",
                    "new_count": 0,
                    "visible_count": 5,
                    "more": False,
                },
            },
        ),
    ],
    [
        (
            "조용하고 주차되는 카페 찾아줘",
            {
                "proposal": QUIET_PROPOSAL,
                "cards": "ABCDEF",
                "parking": None,
                "kinds": ["cafe", "restaurant"],
                "tool": "search_places",
            },
        ),
        (
            "아니 취소해줘",
            {
                "proposal": False,
                "cards": "ABCDEF",
                "parking": None,
                "tool": "resolve_search_proposal",
                "arguments": {"accept": False},
            },
        ),
        (
            "조용하고 주차되는 카페 찾아줘",
            {
                "proposal": QUIET_PROPOSAL,
                "cards": "ABCDEF",
                "tool": "search_places",
            },
        ),
        (
            "조용함은 확인 안 돼도 괜찮아, 제안한 주차 가능한 카페 조건으로 찾아줘",
            {
                "proposal": False,
                "parking": True,
                "cards": "A",
                "kinds": ["cafe"],
                "tool": "resolve_search_proposal",
                "arguments": {"accept": True},
            },
        ),
        ("고마워", {"unchanged": True, "no_tools": True}),
    ],
    [
        (
            "첫 번째 장소는 이미 아는 곳이야",
            {
                "known": 1,
                "excluded": 0,
                "cards": "ABCDEF",
                "tool": "mark_places_known",
                "target": 0,
            },
        )
    ],
]


def contains(actual, expected):
    """Compare structured evidence, allowing unrelated server metadata."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and contains(actual[key], value) for key, value in expected.items()
        )
    return type(actual) is type(expected) and actual == expected


def parking(filters):
    return next(
        (a["value"] for a in filters["required"] if a["attribute"] == "operations.parking"), None
    )


def verify_execution(expected, before, after, turn):
    """Failed attempts may recover, but every completed action must match the intent."""
    statuses = {"applied", "unchanged", "empty", "needs_confirmation"}
    completed = [e for e in turn.executions if e["result"]["status"] in statuses]
    if not completed:
        return ["successful_execution"]
    failed = []
    args = dict(expected.get("arguments", {}))
    if "target" in expected:
        target = expected["target"]
        ref = before["selected_ref"] if target == "selected" else before["cards"][target]["ref"]
        args.update(
            {"place_ref": ref} if expected["tool"] == "select_place" else {"place_refs": [ref]}
        )
        if expected["tool"] == "set_place_excluded":
            args["excluded"] = True
            if ref not in [p["ref"] for p in after["excluded_places"]]:
                failed.append("excluded_target")
        if expected["tool"] == "mark_places_known" and ref not in [
            p["ref"] for p in after["known_places"]
        ]:
            failed.append("known_target")
    if "details" in expected:
        args["attributes"] = list(expected["details"])
    for execution in completed:
        if execution["name"] != expected["tool"]:
            failed.append("unexpected_execution")
            continue
        if not contains(execution["arguments"], args):
            failed.append("tool_arguments")
        result = execution["result"]
        status = (
            "needs_confirmation"
            if expected.get("proposal")
            else ("unchanged" if "details" in expected or "result" in expected else None)
        )
        if status and result["status"] != status:
            failed.append("tool_status")
        if not status and result["status"] == "needs_confirmation":
            failed.append("unexpected_proposal")
        if not contains(result, expected.get("result", {})):
            failed.append("tool_result")
        if "details" in expected:
            facts = result.get("places", [])
            card = before["cards"][expected["target"]]
            if len(facts) != 1 or not contains(
                facts[0],
                {
                    "ref": card["ref"],
                    "name": card["name"],
                    "facts": expected["details"],
                },
            ):
                failed.append("detail_facts")
    return failed


def verify(expected, before, after, turn):
    failed = []
    if turn.status != "ready":
        failed.append("ready")
    if "tool" in expected:
        failed.extend(verify_execution(expected, before, after, turn))
    for name, value in expected.items():
        if name in {"tool", "target", "details", "arguments", "result"}:
            continue
        if name == "kinds":
            actual = sorted(after["filters"]["kinds"])
            value = sorted(value)
        elif name == "parking":
            actual = parking(after["filters"])
        elif name == "cards":
            actual = "".join(p["name"][-1] for p in after["cards"])
        elif name == "selected":
            actual = next(
                (p["name"][-1] for p in after["cards"] if p["ref"] == after["selected_ref"]), None
            )
        elif name == "proposal":
            proposal = after["pending_proposal"]
            if isinstance(value, dict):
                actual = (
                    None
                    if not proposal
                    else {
                        "kinds": sorted(proposal["filters"]["kinds"]),
                        "parking": parking(proposal["filters"]),
                        "apply_to": proposal["apply_to"],
                        "unavailable_contains": value["unavailable_contains"]
                        if any(
                            value["unavailable_contains"] in label
                            for label in proposal["unavailable"]
                        )
                        else None,
                    }
                )
                # A proposal must preserve the committed filters, cards and selection.
                for key in ["filters", "cards", "snapshot_id", "selected_ref"]:
                    if before[key] != after[key]:
                        failed.append("proposal_changed_" + key)
                if proposal:
                    required = [
                        a
                        for a in before["filters"]["required"]
                        if a["attribute"] != "operations.parking"
                    ]
                    required.append(
                        {"attribute": "operations.parking", "op": "eq", "value": value["parking"]}
                    )
                    if proposal["filters"]["required"] != required:
                        failed.append("proposal_required")
                    for key in ["radius_m", "name_query", "preferred", "any_of"]:
                        if proposal["filters"][key] != before["filters"][key]:
                            failed.append("proposal_changed_" + key)
            else:
                actual = proposal is not None
        elif name in {"excluded", "known"}:
            actual = len(after[f"{name}_places"])
        elif name == "unchanged":
            actual = before == after
        elif name == "no_tools":
            actual = not turn.executions
        else:
            raise ValueError(name)
        if actual != value:
            failed.append({name: {"expected": value, "actual": actual}})
    return failed


async def main(args):
    provider = GeminiConversation(read_key(args.key_file), args.model)
    records = []
    for sequence in SCENARIOS if args.scenario is None else [SCENARIOS[args.scenario]]:
        workspace, recent = await new_workspace(), []
        for query, expected in sequence:
            before = ui_view(await workspace.read())
            turn = ToolTurn(str(uuid4()), query, before["revision"])
            started = perf_counter()
            async with asyncio.timeout(90):
                await FacilityDialogue(provider).run(turn, workspace, recent=recent)
            after = ui_view(await workspace.read())
            failed = verify(expected, before, after, turn)
            summary = {
                "evaluation_version": EVALUATION_VERSION,
                "answer_factuality": "manual_review_required",
                "query": query,
                "status": turn.status,
                "answer": turn.response,
                "failed": failed,
                "calls": len(turn.provider_calls),
                "errors": [
                    {k: c[k] for k in ["http_status", "error_type"] if k in c}
                    for c in turn.provider_calls
                    if "error_type" in c
                ],
                "seconds": round(perf_counter() - started, 2),
                "actions": [
                    {
                        "name": e["name"],
                        "arguments": e["arguments"],
                        "status": e["result"]["status"],
                    }
                    for e in turn.executions
                ],
            }
            records.append(
                {
                    **summary,
                    "before": before,
                    "after": after,
                    "trace": turn.provider_calls,
                    "executions": turn.executions,
                }
            )
            args.output.write_text(
                json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps(summary, ensure_ascii=False), flush=True)
            if turn.status == "ready":
                recent.append({"query": query, "answer": turn.response})
    print(json.dumps({"passed": sum(not r["failed"] for r in records), "total": len(records)}))
    return int(any(r["failed"] for r in records))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--model", default="gemini-3.1-flash-lite")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scenario", type=int, choices=range(len(SCENARIOS)))
    raise SystemExit(asyncio.run(main(parser.parse_args())))
