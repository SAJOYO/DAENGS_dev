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
        ("두 번째는 주차 돼?", {"unchanged": True, "tool": "get_place_details"}),
        ("두 번째 장소 선택해줘", {"selected": "B", "tool": "select_place"}),
        ("선택한 곳은 제외해줘", {"cards": "ACDEF", "excluded": 1, "tool": "set_place_excluded"}),
        (
            "다른 곳 보여줘",
            {"cards": "", "parking": None, "kinds": ["cafe", "restaurant"], "tool": "next_places"},
        ),
    ],
    [
        (
            "조용하고 주차되는 카페 찾아줘",
            {"proposal": True, "cards": "ABCDEF", "parking": None, "kinds": ["cafe", "restaurant"]},
        ),
        (
            "아니 취소해줘",
            {
                "proposal": False,
                "cards": "ABCDEF",
                "parking": None,
                "tool": "resolve_search_proposal",
            },
        ),
        ("조용하고 주차되는 카페 찾아줘", {"proposal": True, "cards": "ABCDEF"}),
        (
            "조용함은 확인 안 돼도 괜찮아, 제안한 주차 가능한 카페 조건으로 찾아줘",
            {"proposal": False, "parking": True, "cards": "A", "kinds": ["cafe"]},
        ),
        ("고마워", {"unchanged": True, "no_tools": True}),
    ],
    [
        (
            "첫 번째 장소는 이미 아는 곳이야",
            {"known": 1, "excluded": 0, "cards": "ABCDEF", "tool": "mark_places_known"},
        )
    ],
]


def verify(expected, before, after, turn):
    failed = []
    if turn.status != "ready":
        failed.append("ready")
    for name, value in expected.items():
        if name == "kinds":
            actual = sorted(after["filters"]["kinds"])
            value = sorted(value)
        elif name == "parking":
            actual = next(
                (
                    a["value"]
                    for a in after["filters"]["required"]
                    if a["attribute"] == "operations.parking"
                ),
                None,
            )
        elif name == "cards":
            actual = "".join(p["name"][-1] for p in after["cards"])
        elif name == "selected":
            actual = next(
                (p["name"][-1] for p in after["cards"] if p["ref"] == after["selected_ref"]), None
            )
        elif name == "proposal":
            actual = after["pending_proposal"] is not None
        elif name in {"excluded", "known"}:
            actual = len(after[f"{name}_places"])
        elif name == "unchanged":
            actual = before == after
        elif name == "no_tools":
            actual = not turn.executions
        elif name == "tool":
            actual = value if any(e["name"] == value for e in turn.executions) else None
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--model", default="gemini-3.1-flash-lite")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scenario", type=int, choices=range(len(SCENARIOS)))
    asyncio.run(main(parser.parse_args()))
