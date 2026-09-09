from datetime import UTC, datetime, timedelta

import pytest

from daengs_place.place.conversation.answer import compose_answer
from daengs_place.place.conversation.contract import (
    AnswerDraft,
    AnswerRequest,
    PrepareRequest,
    TurnPlan,
)
from daengs_place.place.conversation.service import ConversationService, snapshot_hits
from tests.place.support.conversation import Planner, Searcher, manual, place


async def test_shopping_pick_then_explain_reuses_snapshot_and_actual_visible_order():
    searcher, planner = Searcher(), Planner()
    service = ConversationService(planner, searcher=searcher)
    initial = await service.prepare(None, manual())
    picked = await service.prepare(
        None, PrepareRequest(mode="chat", query="아무 데나 하나 골라줘", previous=initial.state)
    )
    assert len(searcher.calls) == 1
    assert picked.receipt.execution == "reused"
    assert picked.receipt.selected.ref == "first"
    assert picked.state.filters == initial.state.filters
    planner.next = TurnPlan(goal="explain", reference_index=2)
    explained = await service.prepare(
        None,
        PrepareRequest(
            mode="chat",
            query="두 번째는 왜?",
            previous=picked.state,
            visible_order=tuple(reversed(picked.state.snapshot.display_order)),
        ),
    )
    assert explained.receipt.selected.ref == "first"
    assert len(searcher.calls) == 1
    assert planner.requests[-1].previous.history[-1].goal == "pick_one"


async def test_changed_filter_does_not_refilter_only_cached_top_twenty():
    searcher, planner = Searcher(), Planner()
    searcher.rows = [place(str(i), distance=i + 1, parking=False) for i in range(25)] + [
        place("parking", distance=1000)
    ]
    service = ConversationService(planner, searcher=searcher)
    initial = await service.prepare(None, manual())
    assert len(snapshot_hits(initial.state.snapshot)) == 20
    planner.next = TurnPlan(
        goal="show",
        changes={
            "upsert_all": [
                {"id": "parking", "capability": "operations.parking", "op": "eq", "value": True}
            ]
        },
    )
    filtered = await service.prepare(
        None, PrepareRequest(mode="chat", query="주차되는 곳만", previous=initial.state)
    )
    assert len(searcher.calls) == 2
    assert snapshot_hits(filtered.state.snapshot)[0].place.key.ref == "parking"
    repeated = await service.prepare(
        None, PrepareRequest(mode="chat", query="주차되는 곳만", previous=filtered.state)
    )
    assert repeated.receipt.execution == "reused"
    assert len(searcher.calls) == 2


async def test_clicked_card_can_be_explained_and_old_index_is_not_rebound_after_refresh():
    searcher, planner = Searcher(), Planner(TurnPlan(goal="explain"))
    service = ConversationService(planner, searcher=searcher)
    initial = await service.prepare(None, manual())
    explained = await service.prepare(
        None,
        PrepareRequest(
            mode="chat",
            query="이 장소는 왜?",
            previous=initial.state,
            visible_selected=initial.state.snapshot.display_order[1],
        ),
    )
    assert explained.receipt.selected.ref == "second"
    planner.next = TurnPlan(goal="pick_one", reference_index=2, refresh=True)
    ambiguous = await service.prepare(
        None, PrepareRequest(mode="chat", query="새로 찾아서 두 번째로", previous=initial.state)
    )
    assert ambiguous.receipt.code == "reference_needs_confirmation"
    assert len(searcher.calls) == 1


async def test_manual_and_ai_share_filters_edit_only_marks_old_results_and_failure_preserves_state():
    searcher, planner = Searcher(), Planner()
    service = ConversationService(planner, searcher=searcher)
    initial = await service.prepare(None, manual())
    planner.next = TurnPlan(goal="edit_only", changes={"radius_m": 1000})
    edited = await service.prepare(
        None, PrepareRequest(mode="chat", query="반경만 1km로", previous=initial.state)
    )
    assert edited.receipt.execution == "not_run"
    assert not edited.receipt.result_matches_filters
    assert len(searcher.calls) == 1
    planner.next = TurnPlan(goal="show", changes={"candidate_kinds": ["cafe"]})
    searcher.error = True
    failed = await service.prepare(
        None, PrepareRequest(mode="chat", query="카페로", previous=edited.state)
    )
    assert failed.receipt.execution == "failed"
    assert failed.state.filters == edited.state.filters
    searcher.error = False
    cafe = await service.prepare(
        None, PrepareRequest(mode="chat", query="카페로", previous=edited.state)
    )
    assert cafe.state.filters.candidate_kinds == ("cafe",)
    assert cafe.receipt.returned_count == 1


async def test_expired_snapshot_refreshes_for_search_but_historical_explanation_does_not():
    searcher, planner = Searcher(), Planner()
    now = datetime.now(UTC)
    service = ConversationService(planner, searcher=searcher, now=lambda: now)
    initial = await service.prepare(None, manual())
    service.now = lambda: now + timedelta(seconds=301)
    planner.next = TurnPlan(goal="explain", reference_index=1)
    await service.prepare(
        None, PrepareRequest(mode="chat", query="첫 번째는?", previous=initial.state)
    )
    assert len(searcher.calls) == 1
    planner.next = TurnPlan(goal="pick_one")
    await service.prepare(
        None, PrepareRequest(mode="chat", query="하나 골라줘", previous=initial.state)
    )
    assert len(searcher.calls) == 2


@pytest.mark.parametrize(
    "draft",
    [
        AnswerDraft(text="없는 정보", evidence_ids=("invented",)),
        AnswerDraft(text="테스트 first는 9999m예요.", evidence_ids=("place", "distance")),
    ],
)
async def test_answer_validation_falls_back_without_changing_successful_search(draft):
    service = ConversationService(Planner(), searcher=Searcher())
    initial = await service.prepare(None, manual())
    prepared = await service.prepare(
        None, PrepareRequest(mode="chat", query="골라줘", previous=initial.state)
    )

    class Generator:
        async def answer(self, request):
            return draft

    answer = await compose_answer(
        AnswerRequest(query="골라줘", committed_revision=2, prepared=prepared), Generator()
    )
    assert answer.source == "fallback"
    assert prepared.receipt.execution == "reused"
    assert "테스트 first" in answer.text
