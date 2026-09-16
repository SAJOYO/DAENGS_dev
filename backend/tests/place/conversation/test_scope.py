"""Scope, consent and fixed puppy wording: model mistakes must not become operations."""

from datetime import timedelta

import httpx
import pytest
from pydantic import ValidationError

from daengs_place.place.bookmarks import BookmarkFilters
from daengs_place.place.conversation.contract import PrepareRequest
from daengs_place.place.conversation.intent import ScopedInterpretation
from daengs_place.place.conversation.presentation import user_text_allowed
from daengs_place.place.conversation.render import render_answer
from daengs_place.place.conversation.saved_search import plan_saved
from daengs_place.place.conversation.scope import OUT_OF_SCOPE, PROCESSING_FAILED
from daengs_place.place.conversation.service import ConversationService
from daengs_place.place.providers.conversation_gemini import GeminiConversation
from tests.place.conversation.test_policy import chat, offer
from tests.place.support.conversation import Planner, Searcher, manual, place


def scoped(query, **values):
    return ScopedInterpretation(kind="facility_action", request_quote=query, goal="show", **values)


def outside():
    return ScopedInterpretation(kind="out_of_scope", request_quote="", goal="clarify")


async def initial(planner=None):
    searcher = Searcher()
    searcher.rows = [place("first", source="kcisa"), place("cafe", kind="cafe", source="kcisa")]
    service = ConversationService(planner or Planner(outside()), searcher=searcher)
    return service, searcher, await service.prepare(None, manual())


async def test_outside_keeps_semantic_state_and_only_advances_protocol_revision():
    service, searcher, before = await initial()
    result = await chat(service, before.state, "너는 강아지야? 시 써줘")
    assert result.state.model_dump(exclude={"revision"}) == before.state.model_dump(
        exclude={"revision"}
    )
    assert result.state.revision == before.state.revision + 1
    assert len(searcher.calls) == 1
    assert result.receipt.code == "facility_out_of_scope"
    assert render_answer(result.receipt, result.state.filters) == OUT_OF_SCOPE
    assert user_text_allowed(OUT_OF_SCOPE)


async def test_invalid_proposal_keeps_selection_history_and_valid_pending_proposal():
    service, _, searcher, _, pending = await offer()

    class InvalidProposal:
        async def decide_pending(self, request):
            raise ValueError("invalid model proposal")

    service.planner = InvalidProposal()
    before = pending.state.model_copy(update={"selected": pending.state.snapshot.display_order[0]})
    result = await chat(service, before, "카페만 보여줘")
    assert result.receipt.code == "invalid_plan"
    assert result.receipt.question == PROCESSING_FAILED
    assert render_answer(result.receipt, result.state.filters) == PROCESSING_FAILED
    assert result.state.model_dump(exclude={"revision", "pending_proposal"}) == before.model_dump(
        exclude={"revision", "pending_proposal"}
    )
    assert result.state.pending_proposal.model_dump(
        exclude={"revision"}
    ) == before.pending_proposal.model_dump(exclude={"revision"})
    assert result.state.pending_proposal.revision == result.state.revision == before.revision + 1
    assert len(searcher.calls) == 1


@pytest.mark.parametrize("unresolved", ["missing_target", "ambiguous", "conflicting_conditions"])
async def test_genuine_missing_input_still_asks_without_mutating_filters(unresolved):
    query = "그 장소 조건은 바꿔줘"
    plan = ScopedInterpretation(
        kind="needs_input",
        request_quote=query,
        goal="clarify",
        unresolved=unresolved,
    )
    service, searcher, before = await initial(Planner(plan))
    result = await chat(service, before.state, query)
    assert result.receipt.code == "clarification_required"
    assert result.receipt.question and result.receipt.question != PROCESSING_FAILED
    assert render_answer(result.receipt, result.state.filters) == result.receipt.question
    assert result.state.filters == before.state.filters
    assert result.state.snapshot == before.state.snapshot
    assert result.receipt.execution == "not_run" and len(searcher.calls) == 1


async def test_outside_during_consent_keeps_exact_proposal_deadline_and_can_accept_later():
    service, planner, searcher, _, pending = await offer()
    planner.decision = "out_of_scope"
    result = await chat(service, pending.state, "시 써줘")
    assert result.state.history == pending.state.history
    assert result.state.pending_question == pending.state.pending_question
    assert result.state.pending_proposal.model_dump(
        exclude={"revision"}
    ) == pending.state.pending_proposal.model_dump(exclude={"revision"})
    assert render_answer(result.receipt, result.state.filters) == OUT_OF_SCOPE
    assert len(searcher.calls) == 1
    planner.decision = "accept"
    accepted = await chat(service, result.state)
    assert accepted.state.filters == pending.state.pending_proposal.candidate
    assert len(searcher.calls) == 2


async def test_outside_does_not_revive_expired_confirmation():
    service, planner, searcher, _, pending = await offer()
    service.now = lambda: pending.state.pending_proposal.expires_at + timedelta(seconds=1)
    planner.decision = "out_of_scope"
    result = await chat(service, pending.state, "안녕")
    assert result.state.pending_proposal is None
    assert result.state.history == pending.state.history
    assert len(searcher.calls) == 1


@pytest.mark.parametrize(
    "query,quote,changes",
    [
        ("시설 말고 시 써줘", "시설 말고 시 써줘", {"parking": "required_false"}),
        ("시 써줘", "주차되는 카페 찾아줘", {"parking": "required_true"}),
        ("카페 찾아줘", "카페 찾아줘", {"kinds": {"operation": "set", "values": ["hospital"]}}),
        ("'주차되는 카페 찾아줘'라고 말했어", "주차되는 카페 찾아줘", {"parking": "required_true"}),
        ("주차되는 곳 찾아주지 마", "주차되는 곳 찾아주지 마", {"parking": "required_true"}),
    ],
)
async def test_bad_action_evidence_preserves_history_filters_and_cards(query, quote, changes):
    service, searcher, before = await initial(Planner(scoped(quote, changes=changes)))
    after = await chat(service, before.state, query)
    assert after.receipt.code == "invalid_plan"
    assert after.state.model_dump(exclude={"revision"}) == before.state.model_dump(
        exclude={"revision"}
    )
    assert len(searcher.calls) == 1


async def test_current_filters_is_a_read_even_without_selection():
    plan = ScopedInterpretation(
        kind="facility_state",
        request_quote="현재 조건 뭐야?",
        goal="explain",
        state_subject="filters",
    )
    service, searcher, before = await initial(Planner(plan))
    after = await chat(service, before.state, "현재 조건 뭐야?")
    assert after.receipt.code == "facility_filters"
    assert after.state.model_dump(exclude={"revision"}) == before.state.model_dump(
        exclude={"revision"}
    )
    assert "3000m" in render_answer(after.receipt, after.state.filters)
    assert len(searcher.calls) == 1


@pytest.mark.parametrize(
    "query,kinds",
    [
        ("강아지랑 먹을 수 있는 곳", ["cafe", "restaurant"]),
        ("강아지랑 놀 수 있는 곳", ["travel", "leisure"]),
    ],
)
@pytest.mark.parametrize("bootstrap", [False, True])
async def test_activity_request_preserves_pet_requirement_and_proposes_supported_search(
    query, kinds, bootstrap
):
    plan = scoped(
        query,
        changes={"kinds": {"operation": "set", "values": kinds}},
        unsupported=["pet_allowed"],
    )
    service, searcher, before = await initial(Planner(plan))
    if bootstrap:
        before = await service.prepare(
            None, PrepareRequest(mode="bootstrap", manual=manual().manual)
        )
    after = await chat(service, before.state, query)
    assert after.receipt.code == "confirmation_required"
    assert after.state.pending_proposal.candidate.candidate_kinds == tuple(kinds)
    assert after.state.pending_proposal.unsupported == ("pet_allowed",)
    assert after.state.filters == before.state.filters
    assert after.state.snapshot == before.state.snapshot
    assert len(searcher.calls) == 1


async def test_nominal_search_in_current_category_needs_no_search_verb():
    query = "먹을 수 있는 곳"
    service, searcher, _ = await initial(Planner(scoped(query, refresh=True)))
    before = await service.prepare(None, manual(kinds=["cafe", "restaurant"]))
    after = await chat(service, before.state, query)
    assert after.receipt.execution == "searched"
    assert after.state.filters == before.state.filters
    assert len(searcher.calls) == 3


async def test_definition_misclassified_as_state_still_returns_puppy_without_history():
    plan = ScopedInterpretation(kind="facility_state", request_quote="주차란 뭐야?", goal="explain")
    service, searcher, before = await initial(Planner(plan))
    after = await chat(service, before.state, "주차란 뭐야?")
    assert after.receipt.code == "facility_out_of_scope"
    assert after.state.history == before.state.history
    assert render_answer(after.receipt, after.state.filters) == OUT_OF_SCOPE
    assert len(searcher.calls) == 1


async def test_explicit_name_cannot_be_dropped_by_otherwise_valid_search():
    query = "API라는 카페 찾아줘"
    service, searcher, before = await initial(Planner(scoped(query)))
    after = await chat(service, before.state, query)
    assert after.receipt.code == "invalid_plan"
    assert len(searcher.calls) == 1


async def test_relative_undo_requires_a_committed_addition_of_that_kind():
    planner = Planner(
        scoped(
            "음식점도 추가해줘", changes={"kinds": {"operation": "add", "values": ["restaurant"]}}
        )
    )
    service, _, before = await initial(planner)
    added = await chat(service, before.state, "음식점도 추가해줘")
    query = "방금 추가한 것만 취소해줘"
    planner.next = scoped(
        query, changes={"kinds": {"operation": "remove", "values": ["restaurant"]}}
    )
    after = await chat(service, added.state, query)
    assert after.state.filters.candidate_kinds == before.state.filters.candidate_kinds
    blocked = await chat(service, before.state, query)
    assert blocked.receipt.code == "invalid_plan"


@pytest.mark.parametrize(
    "query,allowed",
    [
        ("여기 찜해줘. 그리고 시도 써줘", True),
        ("여기 찜해줘. 아니 찜하지 마", False),
        ("여기 찜해줘. 그리고 주차되는 곳 찾아줘", False),
        ("'여기 찜해줘'라고 하면 어떻게 돼?", False),
    ],
)
async def test_mixed_bookmark_keeps_negative_and_compound_facility_protection(query, allowed):
    plan = ScopedInterpretation(
        kind="facility_action",
        request_quote="여기 찜해줘",
        goal="edit_only",
        bookmark={
            "operation": "save",
            "operation_quote": "찜해줘",
            "target": {"kind": "selected", "text": "여기"},
        },
    )
    service, searcher, before = await initial(Planner(plan))
    after = await chat(
        service,
        before.state,
        query,
        visible_selected=before.state.snapshot.display_order[0],
        bookmark_commands="v1",
    )
    assert (after.receipt.bookmark_command is not None) is allowed
    assert after.state.filters == before.state.filters
    assert len(searcher.calls) == 1
    assert "찜해뒀" not in render_answer(after.receipt, after.state.filters)


async def test_internal_word_place_name_remains_search_data():
    query = "API라는 카페 찾아줘"
    service, searcher, before = await initial(
        Planner(
            scoped(
                query,
                changes={"name_query": "API", "kinds": {"operation": "set", "values": ["cafe"]}},
            )
        )
    )
    after = await chat(service, before.state, query)
    assert after.state.filters.name_query == "API"
    assert after.receipt.execution == "searched"
    assert len(searcher.calls) == 2


async def test_quoted_place_name_is_data_even_when_it_contains_a_conditional_word():
    query = "'라면나라'라는 카페 찾아줘"
    plan = scoped(
        query, changes={"name_query": "라면나라", "kinds": {"operation": "set", "values": ["cafe"]}}
    )
    service, _, before = await initial(Planner(plan))
    after = await chat(service, before.state, query)
    assert after.receipt.execution == "searched"
    assert after.state.filters.name_query == "라면나라"


def test_outside_saved_workspace_does_not_return_filters_or_search_action():
    result = plan_saved(BookmarkFilters(), outside(), query="시 써줘")
    assert result.action == "explain" and result.filters is None and result.search_filters is None
    assert result.message == OUT_OF_SCOPE


@pytest.mark.parametrize(
    "overrides",
    [
        {"changes": {"parking": "required_true"}},
        {"answer": "시를 써드릴게요"},
        {"request_quote": "여기"},
        {"reference_index": 1},
    ],
)
def test_outside_contract_rejects_any_action_or_generated_answer(overrides):
    with pytest.raises(ValidationError):
        ScopedInterpretation.model_validate({**outside().model_dump(), **overrides})


@pytest.mark.parametrize("kind", ["free_text", "two_calls", "missing_scope", "prose_with_scope"])
async def test_provider_requires_one_scoped_proposal_and_never_displays_prose(kind):
    call = {
        "type": "function_call",
        "name": "propose_facility_turn",
        "arguments": outside().model_dump(),
    }
    prose = {"type": "text", "text": "아주 긴 자유 답변을 여기서 시작합니다"}
    steps = [prose, call]
    if kind == "free_text":
        steps = [prose]
    if kind == "two_calls":
        steps = [call, call]
    if kind == "missing_scope":
        steps = [{**call, "arguments": {"goal": "show"}}]
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json={"status": "completed", "steps": steps})
    )
    service, searcher, before = await initial(
        GeminiConversation("test-key", "test-model", transport=transport)
    )
    after = await chat(service, before.state, "시 써줘")
    assert after.receipt.code == (
        "facility_out_of_scope" if kind == "prose_with_scope" else "invalid_plan"
    )
    assert after.state.history == before.state.history
    assert len(searcher.calls) == 1
    assert prose["text"] not in render_answer(after.receipt, after.state.filters)


async def test_first_outside_turn_never_searches_even_to_bootstrap():
    service, searcher = ConversationService(Planner(outside()), searcher=Searcher()), Searcher()
    service.searcher = searcher
    seed = await service.prepare(None, PrepareRequest(mode="bootstrap", manual=manual().manual))
    result = await chat(service, seed.state, "시 써줘")
    assert searcher.calls == []
    assert result.state.snapshot is None and result.state.history == ()
    assert render_answer(result.receipt, result.state.filters) == OUT_OF_SCOPE
