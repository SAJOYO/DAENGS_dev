"""Executable policy invariants, including opaque identities and stale proposals."""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from daengs_place.place.conversation.answer import compose_answer
from daengs_place.place.conversation.compiler import compile_changes
from daengs_place.place.conversation.contract import AnswerRequest, PrepareRequest
from daengs_place.place.conversation.intent import Interpretation, PendingDecision, SemanticChanges
from daengs_place.place.conversation.service import ConversationService
from daengs_place.place.filters.evaluation import evaluate
from tests.place.support.conversation import Planner, Searcher, manual, place


class Interpreter(Planner):
    decision = "accept"

    async def decide_pending(self, request):
        return PendingDecision(decision=self.decision)


async def offer():
    planner = Interpreter(
        Interpretation(
            goal="show",
            changes={"kinds": {"operation": "set", "values": ["cafe"]}, "parking": "required_true"},
            unsupported=("quiet",),
        )
    )
    searcher = Searcher()
    service = ConversationService(planner, searcher=searcher)
    initial = await service.prepare(None, manual(kinds=["cafe", "restaurant"]))
    pending = await service.prepare(
        None,
        PrepareRequest(
            mode="chat",
            query="조용하고 주차되는 카페 찾아줘",
            previous=initial.state,
        ),
    )
    return service, planner, searcher, initial, pending


async def chat(service, state, query="응", **kwargs):
    return await service.prepare(
        None, PrepareRequest(mode="chat", previous=state, query=query, **kwargs)
    )


async def test_accept_replays_exact_validated_candidate_without_replanning():
    service, planner, searcher, initial, pending = await offer()
    proposal = pending.state.pending_proposal
    assert pending.receipt.action == "await_confirmation"
    assert pending.state.filters == initial.state.filters
    assert pending.state.snapshot == initial.state.snapshot
    assert len(searcher.calls) == 1
    assert "카페" in proposal.question and "주차 가능" in proposal.question
    assert "음식점" not in proposal.question
    # Any new filter generation would now choose the wrong category; acceptance cannot call it.
    planner.next = Interpretation(
        goal="show", changes={"kinds": {"operation": "set", "values": ["restaurant"]}}
    )
    accepted = await chat(service, pending.state)
    assert len(planner.requests) == 1
    assert accepted.state.filters == proposal.candidate
    assert accepted.state.pending_proposal is None
    assert accepted.receipt.action == "execute" and len(searcher.calls) == 2


@pytest.mark.parametrize("decision", ["reject", "unclear"])
async def test_reject_or_unclear_cannot_change_state_or_search(decision):
    service, planner, searcher, initial, pending = await offer()
    planner.decision = decision
    resolved = await chat(service, pending.state)
    assert resolved.state.filters == initial.state.filters
    assert resolved.state.snapshot == initial.state.snapshot
    assert len(searcher.calls) == 1
    if decision == "reject":
        assert resolved.state.pending_proposal is None
    else:
        assert resolved.state.pending_proposal.id == pending.state.pending_proposal.id
        assert resolved.state.pending_proposal.revision == resolved.state.revision
        planner.decision = "accept"
        accepted = await chat(service, resolved.state)
        assert accepted.state.filters == pending.state.pending_proposal.candidate


async def test_revision_mismatch_and_expiry_block_acceptance_but_new_request_can_continue():
    service, planner, searcher, _, pending = await offer()
    stale = await chat(service, pending.state, base_revision=pending.state.revision + 1)
    assert stale.receipt.code == "pending_expired" and len(searcher.calls) == 1
    service.now = lambda: pending.state.pending_proposal.expires_at + timedelta(seconds=1)
    expired = await chat(service, pending.state)
    assert expired.state.pending_proposal is None and expired.state.filters == pending.state.filters
    assert expired.receipt.code == "pending_expired"
    planner.decision = "new_request"
    planner.next = Interpretation(goal="show", changes={"parking": "required_false"})
    fresh = await chat(service, pending.state, "주차 없는 곳만")
    assert fresh.receipt.execution == "searched" and fresh.state.pending_proposal is None


async def test_revision_of_proposal_preserves_parking_and_requires_new_confirmation():
    service, planner, searcher, initial, pending = await offer()
    planner.decision = "revise"
    planner.next = Interpretation(
        goal="show", changes={"kinds": {"operation": "set", "values": ["restaurant"]}}
    )
    revised = await chat(service, pending.state, "응 근데 음식점으로")
    assert revised.state.filters == initial.state.filters
    proposal = revised.state.pending_proposal
    assert proposal.id != pending.state.pending_proposal.id
    assert proposal.candidate.candidate_kinds == ("restaurant",)
    assert evaluate(proposal.candidate, "restaurant", True, False) is True
    assert evaluate(proposal.candidate, "restaurant", False, False) is False
    assert "음식점" in proposal.question and "주차 가능" in proposal.question
    assert len(searcher.calls) == 1


async def test_manual_edit_invalidates_pending_and_search_failure_keeps_accepted_proposal():
    service, _, searcher, _, pending = await offer()
    searcher.error = True
    failed = await chat(service, pending.state)
    assert failed.receipt.execution == "failed"
    assert failed.state.filters == pending.state.filters
    assert failed.state.pending_proposal.id == pending.state.pending_proposal.id
    assert failed.state.pending_proposal.revision == failed.state.revision
    searcher.error = False
    changed = await service.prepare(None, manual(previous=failed.state, radius_m=1000))
    assert changed.state.pending_proposal is None


@pytest.mark.parametrize(
    "parking,status,phrase",
    [
        (True, "known", "주차할 수 있다고"),
        (False, "known", "주차할 수 없다고"),
        (None, "unknown", "정보는 없어서"),
    ],
)
async def test_selected_parking_question_cannot_mutate_filters_and_opaque_facts_are_distinct(
    parking, status, phrase
):
    searcher = Searcher()
    searcher.rows = [place("f81d", parking=parking).model_copy(update={"name": "테스트 장소"})]
    planner = Interpreter(
        Interpretation(
            goal="explain", asked_attributes=("parking",), changes={"parking": "required_false"}
        )
    )
    service = ConversationService(planner, searcher=searcher)
    initial = await service.prepare(None, manual())
    prepared = await chat(
        service, initial.state, "여기 주차 안 돼?", visible_selected=searcher.rows[0].key
    )
    assert prepared.state.filters == initial.state.filters and len(searcher.calls) == 1
    fact = prepared.receipt.facts[0]
    assert (fact.status, fact.value, fact.source) == (status, parking, searcher.rows[0].key)
    answer = await compose_answer(
        AnswerRequest(query="여기 주차 안 돼?", committed_revision=2, prepared=prepared)
    )
    assert phrase in answer.text


async def test_unsupported_answer_and_missing_selection_reason_never_use_unverified_prose():
    planner = Interpreter(
        Interpretation(goal="explain", asked_attributes=("quiet", "free", "selection_reason"))
    )
    service = ConversationService(planner, searcher=Searcher())
    initial = await service.prepare(None, manual())
    explained = await chat(service, initial.state, "여기 조용하고 무료야? 왜 골랐어?")

    class Untrusted:
        async def answer(self, request):
            pytest.fail("unverified prose generation must not be called")

    answer = await compose_answer(
        AnswerRequest(query="여기 조용하고 무료야?", committed_revision=2, prepared=explained),
        Untrusted(),
    )
    assert "조용함" in answer.text and "무료 여부" in answer.text
    assert "확인할 수 없어요" in answer.text and "이유는 기록되어 있지" in answer.text


async def test_clear_filter_edits_compile_scoped_or_without_model_ids_or_duplicates():
    initial = await ConversationService(searcher=Searcher()).prepare(
        None, manual(kinds=["cafe", "restaurant"])
    )
    changes = SemanticChanges(
        alternatives=(
            {"kinds": ["cafe"], "parking": True},
            {"kinds": ["restaurant"], "exclusive": True},
        )
    )
    result = compile_changes(initial.state.filters, changes)
    for kind, parking, exclusive, expected in [
        ("cafe", True, False, True),
        ("cafe", False, True, False),
        ("restaurant", False, True, True),
        ("restaurant", True, False, False),
    ]:
        assert evaluate(result, kind, parking, exclusive) is expected
    assert compile_changes(result, changes) == result
    atoms = [a for b in result.hard.any for a in b.all]
    assert len({a.id for a in atoms} | {b.id for b in result.hard.any}) == len(atoms) + len(
        result.hard.any
    )
    cleared = compile_changes(result, SemanticChanges(parking="clear"))
    assert evaluate(cleared, "cafe", None, None) is True
    assert evaluate(cleared, "restaurant", True, False) is False
    with pytest.raises(ValueError, match="explicit OR"):
        compile_changes(result, SemanticChanges(kinds={"operation": "set", "values": ["hotel"]}))
    with pytest.raises(ValidationError):
        SemanticChanges.model_validate({"upsert_all": [{"id": "model-owned"}]})


async def test_contradiction_and_region_are_exclusive_and_do_not_erase_ordinary_name_search():
    planner = Interpreter(
        Interpretation(
            goal="show", unresolved="conflicting_conditions", changes={"parking": "required_false"}
        )
    )
    searcher = Searcher()
    service = ConversationService(planner, searcher=searcher)
    initial = await service.prepare(None, manual())
    conflict = await chat(service, initial.state, "주차 필수인데 주차 없는 곳만")
    assert conflict.receipt.action == "clarify" and conflict.state.filters == initial.state.filters
    planner.next = Interpretation(
        goal="show", region_query="제주도", changes={"name_query": "제주도"}
    )
    region = await chat(service, initial.state, "제주도에서 찾아줘")
    assert region.receipt.action == "unsupported" and region.state.filters == initial.state.filters
    assert "지도" in region.receipt.question and len(searcher.calls) == 1
    planner.next = Interpretation(goal="show", changes={"name_query": "제주도"})
    named = await chat(service, initial.state, "이름이 제주도인 곳")
    assert named.state.filters.name_query == "제주도" and len(searcher.calls) == 2


async def test_model_misclassified_question_cannot_authorize_saved_change():
    service, planner, searcher, initial, pending = await offer()
    planner.decision = "accept"  # Actual failure observed in the first live policy run.
    planner.next = Interpretation(goal="explain", asked_attributes=("parking",))
    answered = await chat(service, pending.state, "선택한 이 카페 주차 가능해?")
    assert answered.receipt.action == "explain"
    assert answered.state.filters == initial.state.filters and len(searcher.calls) == 1
    planner.next = Interpretation(goal="show", changes={"parking": "required_false"})
    blocked = await chat(service, pending.state, "음... 일단 고민 좀 해볼게")
    assert blocked.receipt.action == "await_confirmation"
    assert blocked.state.pending_proposal.id == pending.state.pending_proposal.id
    assert blocked.state.filters == initial.state.filters and len(searcher.calls) == 1


async def test_bare_consent_without_pending_never_calls_interpreter():
    service = ConversationService(searcher=Searcher())
    initial = await service.prepare(None, manual())
    blocked = await chat(service, initial.state, "응")
    assert blocked.receipt.code == "no_pending_proposal"
    assert blocked.receipt.execution == "not_run"


async def test_expiry_during_consent_classification_prevents_search():
    service, planner, searcher, _, pending = await offer()

    async def expires(request):
        service.now = lambda: pending.state.pending_proposal.expires_at
        return PendingDecision(decision="accept")

    planner.decide_pending = expires
    expired = await chat(service, pending.state)
    assert expired.receipt.code == "pending_expired"
    assert expired.state.filters == pending.state.filters and len(searcher.calls) == 1


async def test_facet_clear_cannot_also_wipe_unrelated_or_conditions():
    service = ConversationService(searcher=Searcher())
    initial = await service.prepare(None, manual(kinds=["cafe", "restaurant"]))
    filtered = compile_changes(
        initial.state.filters,
        SemanticChanges(
            alternatives=(
                {"kinds": ["cafe"], "parking": True},
                {"kinds": ["restaurant"], "exclusive": True},
            )
        ),
    )
    with pytest.raises(ValueError, match="separately"):
        compile_changes(filtered, SemanticChanges(parking="clear", alternatives=()))
