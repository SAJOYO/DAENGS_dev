from datetime import UTC, datetime, timedelta

import pytest

from daengs_place.place.conversation.context import screen_context
from daengs_place.place.conversation.contract import ExplorationState, PrepareRequest
from daengs_place.place.conversation.intent import Interpretation
from daengs_place.place.conversation.render import render_answer
from daengs_place.place.conversation.service import ConversationService
from tests.place.support.conversation import Planner, Searcher, manual, place


def refs(prepared):
    return [key.ref for key in prepared.state.snapshot.display_order]


async def chat(service, previous, **intent):
    service.planner.next = Interpretation(goal="show", **intent)
    return await service.prepare(
        None, PrepareRequest(mode="chat", query="현재 요청", previous=previous.state)
    )


def make_service():
    searcher = Searcher()
    searcher.rows = [place(str(i), distance=i + 1, parking=i == 25) for i in range(26)]
    return ConversationService(Planner(), searcher=searcher), searcher


async def test_next_pages_exhaustion_then_explicit_filter_can_return_seen_candidate():
    service, searcher = make_service()
    first = await service.prepare(None, manual())
    second = await chat(service, first, browse="next")
    end = await chat(service, second, browse="next")
    assert refs(first) == [str(i) for i in range(20)]
    assert refs(second) == [str(i) for i in range(20, 26)]
    assert refs(end) == []
    assert first.receipt.remaining == "more"
    assert second.receipt.remaining == end.receipt.remaining == "exhausted"
    assert len(second.receipt.new_places) == 6
    assert len(end.state.exploration.presented) == 26
    assert "6곳" in render_answer(second.receipt)
    assert "더 찾지 못" in render_answer(end.receipt)
    parking = await chat(service, end, changes={"parking": "required_true"})
    assert refs(parking) == ["25"]
    assert len(searcher.calls) == 4


async def test_next_page_pick_reuses_display_but_normal_show_reopens_same_filter_pool():
    service, searcher = make_service()
    first = await service.prepare(None, manual())
    second = await chat(service, first, browse="next")
    service.planner.next = Interpretation(goal="pick_one")
    picked = await service.prepare(
        None, PrepareRequest(mode="chat", query="하나 골라줘", previous=second.state)
    )
    assert picked.receipt.selected.ref == "20" and len(searcher.calls) == 2
    end = await chat(service, picked, browse="next")
    assert refs(end) == []
    reopened = await chat(service, end)
    assert refs(reopened) == refs(first)
    assert reopened.receipt.execution == "searched"


async def test_exclude_precedes_limit_persists_on_manual_change_and_restores():
    service, _ = make_service()
    first = await service.prepare(None, manual())
    excluded = await chat(
        service, first, place_edit={"operation": "exclude", "indices": list(range(1, 21))}
    )
    assert refs(excluded) == [str(i) for i in range(20, 26)]
    assert len(excluded.receipt.excluded_places) == 20
    assert excluded.receipt.filters_changed is False
    moved = await service.prepare(None, manual(excluded.state, radius_m=2000))
    assert refs(moved) == refs(excluded)
    restored = await chat(service, moved, place_edit={"operation": "restore", "indices": [1]})
    assert refs(restored)[0] == "0"
    assert len(restored.state.exploration.excluded) == 19
    assert len(restored.receipt.restored_places) == 1


async def test_exclusion_resolves_actual_display_order_and_restart_clears_only_exploration():
    service, _ = make_service()
    first = await service.prepare(None, manual())
    service.planner.next = Interpretation(
        goal="show", place_edit={"operation": "exclude", "indices": [1]}, browse="next"
    )
    edited = await service.prepare(
        None,
        PrepareRequest(
            mode="chat",
            query="첫 장소 빼고 더",
            previous=first.state,
            visible_order=tuple(reversed(first.state.snapshot.display_order)),
        ),
    )
    assert edited.state.exploration.excluded[0].key.ref == "19"
    assert refs(edited) == [str(i) for i in range(20, 26)]
    restarted = await chat(service, edited, browse="restart")
    assert restarted.state.exploration.excluded == ()
    assert refs(restarted) == refs(first)
    assert restarted.state.filters == first.state.filters


async def test_failure_does_not_consume_page_or_commit_exclusion_then_retry_succeeds():
    service, searcher = make_service()
    first = await service.prepare(None, manual())
    searcher.error = True
    failed = await chat(
        service, first, browse="next", place_edit={"operation": "exclude", "indices": [1]}
    )
    assert failed.receipt.execution == "failed"
    assert failed.receipt.remaining == "unknown"
    assert failed.receipt.excluded_places == ()
    assert failed.state.exploration == first.state.exploration
    assert refs(failed) == refs(first)
    assert "완료하지 못" in render_answer(failed.receipt)
    searcher.error = False
    retried = await chat(
        service, failed, browse="next", place_edit={"operation": "exclude", "indices": [1]}
    )
    assert refs(retried) == [str(i) for i in range(20, 26)]


async def test_expired_snapshot_next_still_omits_presented_and_excluded():
    service, _ = make_service()
    now = datetime.now(UTC)
    service.now = lambda: now
    first = await service.prepare(None, manual())
    excluded = await chat(service, first, place_edit={"operation": "exclude", "indices": [1]})
    service.now = lambda: now + timedelta(seconds=400)
    next_page = await chat(service, excluded, browse="next")
    assert refs(next_page) == [str(i) for i in range(21, 26)]


async def test_context_contains_names_and_selection_without_raw_action_history():
    service, _ = make_service()
    first = await service.prepare(None, manual())
    request = PrepareRequest(
        mode="chat",
        query="여기 빼줘",
        previous=first.state,
        visible_order=tuple(reversed(first.state.snapshot.display_order)),
        visible_selected=first.state.snapshot.display_order[0],
    )
    context = screen_context(request)
    assert context["current_places"][0]["name"] == "테스트 19"
    assert context["selected"]["ref"] == "0"
    assert "history" not in context


async def test_invalid_target_and_bounded_record_do_not_report_exhaustion():
    service, searcher = make_service()
    first = await service.prepare(None, manual())
    invalid = await chat(service, first, place_edit={"operation": "exclude", "indices": [21]})
    assert invalid.receipt.code == "invalid_exploration_target"
    assert invalid.state.exploration == first.state.exploration
    assert len(searcher.calls) == 1
    from daengs_place.place.contracts import PlaceRef

    full = first.model_copy(
        update={
            "state": first.state.model_copy(
                update={
                    "exploration": ExplorationState(
                        fingerprint=first.state.snapshot.fingerprint,
                        presented=tuple(PlaceRef(source="test", ref=str(i)) for i in range(1200)),
                    )
                }
            )
        }
    )
    bounded = await chat(service, full, browse="next")
    assert bounded.receipt.code == "exploration_budget"
    assert bounded.receipt.remaining == "unknown"


async def test_legacy_session_next_uses_saved_snapshot_and_same_filter_refresh_keeps_seen():
    service, _ = make_service()
    first = await service.prepare(None, manual())
    legacy = first.model_copy(
        update={"state": first.state.model_copy(update={"exploration": ExplorationState()})}
    )
    second = await chat(service, legacy, browse="next")
    assert refs(second) == [str(i) for i in range(20, 26)]
    refreshed = await chat(service, second, refresh=True)
    assert refs(refreshed) == refs(first)
    end = await chat(service, refreshed, browse="next")
    assert refs(end) == []


@pytest.mark.parametrize("operation", ["exclude", "restore"])
async def test_exploration_with_unsupported_requirement_is_not_partially_applied(operation):
    service, _ = make_service()
    first = await service.prepare(None, manual())
    result = await chat(
        service, first, place_edit={"operation": operation, "indices": [1]}, unsupported=["quiet"]
    )
    assert result.receipt.code == "exploration_needs_supported_request"
    assert result.state.filters == first.state.filters
    assert result.state.exploration == first.state.exploration
