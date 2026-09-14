import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from daengs_place.place.commands.view import context, references


async def execute(port, name, args, command_id=None):
    return await port.execute(
        command_id or f"command-{len(port.records)}", name, args, port.state.revision
    )


async def test_add_replace_clear_preserve_unmentioned_conditions(workspace):
    r = await execute(
        workspace,
        "search_places",
        {"category": {"operation": "set", "values": ["cafe"]}, "parking": "required"},
    )
    assert [p.name for p in references(r.state).values()] == ["평가 장소 A"]
    assert r.state.filters.candidate_kinds == ("cafe",)
    r = await execute(
        workspace, "search_places", {"category": {"operation": "add", "values": ["restaurant"]}}
    )
    assert {p.name for p in references(r.state).values()} == {"평가 장소 A", "평가 장소 E"}
    assert r.state.filters.hard.all[0].value is True
    r = await execute(
        workspace, "search_places", {"category": {"operation": "set", "values": ["restaurant"]}}
    )
    assert [p.name for p in references(r.state).values()] == ["평가 장소 E"]
    r = await execute(workspace, "search_places", {"parking": "clear"})
    assert len(references(r.state)) == 3
    assert r.state.filters.spatial.radius_m == 3000
    assert not r.state.filters.hard.all


async def test_details_preserve_selection_filters_results_and_unknown(workspace):
    refs = list(references(workspace.state))
    await execute(workspace, "select_place", {"place_ref": refs[0]})
    before = workspace.state.model_dump(mode="json")
    r = await execute(
        workspace,
        "get_place_details",
        {"place_refs": refs[1:3], "attributes": ["parking", "quiet"]},
    )
    assert r.state.model_dump(mode="json") == before
    assert r.data["places"][0]["facts"]["parking"] == {"status": "known", "value": False}
    assert r.data["places"][1]["facts"]["parking"] == {"status": "unknown", "value": None}
    assert r.data["places"][1]["facts"]["quiet"]["status"] == "unsupported"


async def test_same_conditions_are_success_not_failure(workspace):
    args = {"parking": "required"}
    first = await execute(workspace, "search_places", args)
    again = await execute(workspace, "search_places", args)
    assert again.status == "applied"
    assert again.state.filters == first.state.filters
    assert "required" not in again.changes


async def test_empty_search_is_not_failed(workspace):
    r = await execute(workspace, "search_places", {"name_query": "존재하지않는시설"})
    assert r.status == "empty"
    assert r.state.filters.name_query == "존재하지않는시설"


async def test_failed_search_does_not_commit_filters_or_results(workspace):
    before = workspace.state

    async def fail(*args, **kwargs):
        raise TimeoutError

    workspace.commands.searcher = fail
    r = await execute(workspace, "search_places", {"parking": "required"})
    assert r.status == "failed"
    assert workspace.state == before


async def test_next_never_repeats_presented_places(workspace):
    before = workspace.state
    r = await execute(workspace, "next_places", {})
    assert r.status == "empty"
    assert r.state.filters == before.filters
    assert not references(r.state)
    assert r.state.presented == before.presented


async def test_old_place_ref_cannot_select_new_second_place(workspace):
    ref = list(references(workspace.state))[1]
    await execute(workspace, "search_places", {"parking": "required"})
    before = workspace.state
    r = await execute(workspace, "select_place", {"place_ref": ref})
    assert r.status == "conflict"
    assert workspace.state == before


async def test_only_filters_edit_marks_results_outdated(workspace):
    snapshot = workspace.state.snapshot_id
    r = await execute(
        workspace, "search_places", {"parking": "required", "apply_to": "filters_only"}
    )
    assert r.state.snapshot_id == snapshot
    assert not context(r.state)["results_match_filters"]
    assert (await execute(workspace, "next_places", {})).status == "conflict"


async def test_unavailable_condition_proposes_without_applying(workspace):
    before = workspace.state
    r = await execute(
        workspace, "search_places", {"parking": "required", "unavailable": ["조용함"]}
    )
    assert r.status == "needs_confirmation"
    assert r.state.filters == before.filters and r.state.result == before.result
    assert r.state.proposal.unavailable == ("조용함",)
    r = await execute(workspace, "resolve_search_proposal", {"accept": True})
    assert r.status == "applied" and r.state.proposal is None
    assert all(p.facts.parking is True for p in references(r.state).values())


async def test_new_manual_change_invalidates_pending_proposal(workspace):
    await execute(workspace, "propose_search_change", {"radius_m": 5000})
    await execute(workspace, "search_places", {"parking": "required"})
    before = workspace.state
    r = await execute(workspace, "resolve_search_proposal", {"accept": True})
    assert r.status == "conflict" and workspace.state == before


async def test_expired_proposal_cannot_execute(workspace):
    now = datetime.now(UTC)
    workspace.commands.now = lambda: now
    await execute(workspace, "propose_search_change", {"radius_m": 5000})
    workspace.commands.now = lambda: now + timedelta(minutes=6)
    r = await execute(workspace, "resolve_search_proposal", {"accept": True})
    assert r.status == "conflict" and r.state.filters.spatial.radius_m == 3000


async def test_known_and_excluded_have_different_effects(workspace):
    ref = next(iter(references(workspace.state)))
    await execute(workspace, "mark_places_known", {"place_refs": [ref]})
    assert len(references(workspace.state)) == 6 and not workspace.state.excluded
    await execute(workspace, "set_place_excluded", {"place_refs": [ref], "excluded": True})
    assert len(references(workspace.state)) == 5 and len(workspace.state.known) == 1
    await execute(workspace, "set_place_excluded", {"place_refs": [ref], "excluded": False})
    assert len(references(workspace.state)) == 6


async def test_manual_change_wins_over_slow_search(workspace):
    started, finish = asyncio.Event(), asyncio.Event()
    original = workspace.commands.searcher

    async def slow(*args, **kwargs):
        started.set()
        await finish.wait()
        return await original(*args, **kwargs)

    workspace.commands.searcher = slow
    task = asyncio.create_task(execute(workspace, "search_places", {"parking": "required"}))
    await started.wait()
    ref = next(iter(references(workspace.state)))
    manual = await execute(workspace, "select_place", {"place_ref": ref}, "manual")
    finish.set()
    assert (await task).status == "conflict"
    assert workspace.state == manual.state and not workspace.state.filters.hard.all


async def test_duplicate_request_uses_committed_result_once(workspace):
    revision = workspace.state.revision
    before = len(workspace.commands.searcher.calls)
    args = {"parking": "required"}
    a, b = await asyncio.gather(
        *(workspace.execute("same", "search_places", args, revision) for _ in range(2))
    )
    assert a == b and len(workspace.commands.searcher.calls) == before + 1
    r = await workspace.execute("same", "search_places", {"parking": "clear"}, revision)
    assert r.status == "conflict"


@pytest.mark.parametrize(
    "arguments",
    [
        {"parking": "yes"},
        {"radius_m": 99},
        {"radius_m": "3000"},
        {"latitude": 37.5},
        {"category": {"operation": "remove", "values": ["cafe", "restaurant"]}},
    ],
)
async def test_invalid_arguments_never_change_state(workspace, arguments):
    before = workspace.state
    with pytest.raises((ValidationError, ValueError)):
        await execute(workspace, "search_places", arguments)
    assert workspace.state == before
