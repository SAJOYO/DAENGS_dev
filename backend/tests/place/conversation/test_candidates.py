from datetime import UTC, datetime, timedelta

import pytest

from daengs_place.place.conversation.contract import PrepareRequest
from daengs_place.place.conversation.intent import Interpretation
from daengs_place.place.conversation.render import render_answer
from daengs_place.place.conversation.service import ConversationService
from tests.place.support.conversation import Planner, Searcher, manual, place


def setup():
    searcher = Searcher()
    searcher.rows = [place(str(i), kind="cafe", source="kcisa", distance=i + 1) for i in range(46)]
    return ConversationService(Planner(), searcher=searcher), searcher


def refs(result):
    return [k.ref for k in result.state.snapshot.display_order]


def knowledge(text="첫 번째", kind="ordinal", quote="첫 번째 이미 알아"):
    return {"quote": quote, "targets": [{"kind": kind, "text": text}]}


def test_familiarity_evidence_does_not_require_a_redundant_label():
    intent = Interpretation(goal="explain", familiarity=knowledge())
    assert intent.feedback == "familiarity"


async def initial(service, bookmarks=()):
    return await service.prepare(
        None,
        manual(kinds=["cafe"]).model_copy(
            update={"candidate_pools": "v1", "bookmark_keys": bookmarks}
        ),
    )


async def chat(service, old, query="보여줘", *, bookmarks=(), **meaning):
    service.planner.next = Interpretation(goal=meaning.pop("goal", "show"), **meaning)
    return await service.prepare(
        None,
        PrepareRequest(
            mode="chat",
            query=query,
            previous=old.state,
            candidate_pools="v1",
            bookmark_keys=bookmarks,
        ),
    )


async def new_page(service, old, bookmarks=()):
    return await chat(
        service,
        old,
        "새로운 곳 보여줘",
        bookmarks=bookmarks,
        search_scope="new_candidates",
        search_scope_quote="새로운 곳",
    )


async def test_bookmarked_first_page_is_removed_before_limit_and_next_uses_same_pool():
    service, searcher = setup()
    saved = tuple(p.key for p in searcher.rows[:20])
    first = await initial(service, saved)
    new = await new_page(service, first, saved)
    assert refs(new) == [str(i) for i in range(20, 40)]
    assert new.state.search_pool == "new_candidates"
    more = await chat(service, new, "더 보여줘", bookmarks=saved, browse="next")
    assert refs(more) == [str(i) for i in range(40, 46)]
    assert more.receipt.remaining == "exhausted"


async def test_one_known_correction_keeps_other_cards_and_does_not_bookmark_or_exclude():
    service, _ = setup()
    first = await new_page(service, await initial(service))
    known = await chat(
        service,
        first,
        "첫 번째 이미 알아",
        goal="explain",
        feedback="familiarity",
        familiarity=knowledge(),
    )
    assert refs(known) == [str(i) for i in range(1, 21)]
    assert known.state.exploration.known[0].key.ref == "0"
    assert not known.state.exploration.excluded
    assert known.receipt.bookmark_command is None
    assert "이미 아는 곳" in render_answer(known.receipt)


async def test_familiarity_and_explicit_condition_apply_together():
    service, searcher = setup()
    searcher.rows[0].facts.parking = False
    first = await new_page(service, await initial(service))
    result = await chat(
        service,
        first,
        "첫 번째 이미 알아. 주차 되는 카페 보여줘",
        feedback="familiarity",
        familiarity=knowledge(),
        search_request_quote="주차 되는 카페 보여줘",
        changes={"parking": "required_true"},
    )
    assert result.state.filters.hard.all[0].value is True
    assert result.state.exploration.known[0].key.ref == "0"
    assert "0" not in refs(result)
    assert result.state.search_pool == "new_candidates"


async def test_known_correction_after_edit_only_does_not_keep_cards_from_old_filters():
    service, searcher = setup()
    searcher.rows = [place(str(i), kind="cafe", source="kcisa", parking=i >= 20) for i in range(40)]
    old = await new_page(service, await initial(service))
    changed = await chat(
        service, old, "주차 조건만 넣어줘", goal="edit_only", changes={"parking": "required_true"}
    )
    assert not changed.receipt.result_matches_filters
    assert refs(changed) == refs(old)
    corrected = await chat(
        service, changed, "첫 번째 이미 알아", feedback="familiarity", familiarity=knowledge()
    )
    assert corrected.receipt.result_matches_filters
    assert refs(corrected) == [str(i) for i in range(20, 40)]
    assert corrected.state.exploration.known[0].key.ref == "0"


async def test_known_survives_manual_condition_changes_and_only_affects_new_pool():
    service, _ = setup()
    old = await initial(service)
    known = await chat(
        service,
        old,
        "첫 번째 이미 알아",
        goal="explain",
        feedback="familiarity",
        familiarity=knowledge(),
    )
    assert refs(known) == refs(old) and known.receipt.result_matches_filters
    assert known.receipt.execution == "not_run"
    new = await new_page(service, known)
    changed = await service.prepare(
        None,
        manual(new.state, kinds=["cafe"], radius_m=5000).model_copy(
            update={"candidate_pools": "v1", "bookmark_keys": ()}
        ),
    )
    assert changed.state.exploration.known == known.state.exploration.known
    assert "0" not in refs(changed)
    all_places = await chat(
        service,
        changed,
        "전체 장소 보여줘",
        search_scope="all_places",
        search_scope_quote="전체 장소",
    )
    assert "0" in refs(all_places)
    unsaved = await chat(
        service,
        all_places,
        "찜한 곳 빼고",
        search_scope="unbookmarked",
        search_scope_quote="찜한 곳 빼고",
    )
    assert "0" in refs(unsaved)


async def test_lookup_failure_keeps_knowledge_then_retry_still_omits_it():
    service, searcher = setup()
    old = await new_page(service, await initial(service))
    searcher.error = True
    failed = await chat(
        service, old, "첫 번째 이미 알아", feedback="familiarity", familiarity=knowledge()
    )
    assert failed.receipt.execution == "failed"
    assert refs(failed) == refs(old)
    assert failed.state.exploration.known[0].key.ref == "0"
    assert not failed.receipt.result_matches_filters
    assert "이미 아는 곳" in render_answer(failed.receipt)
    unclear = await chat(service, failed, "응")
    assert not unclear.receipt.result_matches_filters
    assert unclear.state.exploration.known == failed.state.exploration.known
    searcher.error = False
    retried = await chat(service, failed)
    assert "0" not in refs(retried) and retried.receipt.execution == "searched"


async def test_bookmark_failure_is_not_empty_and_changed_bookmarks_invalidate_cache():
    service, searcher = setup()
    old = await initial(service)
    failed = await new_page(service, old, None)
    assert failed.receipt.code == "candidate_pool_unavailable"
    assert failed.state.search_pool == "all_places" and len(searcher.calls) == 1
    new = await new_page(service, failed)
    saved = (searcher.rows[0].key,)
    changed = await chat(service, new, bookmarks=saved)
    assert changed.receipt.execution == "searched" and "0" not in refs(changed)
    failed_read = await chat(service, changed, bookmarks=None)
    assert refs(failed_read) == refs(changed)
    assert failed_read.state.search_pool == "new_candidates"
    unsaved = await chat(service, failed_read, bookmarks=())
    assert "0" in refs(unsaved)


@pytest.mark.parametrize(
    "query,correction",
    [
        ("이미 알아", None),
        ("여기 이미 알아", knowledge("여기", "selected", "여기 이미 알아")),
        ("첫 번째 이미 알아?", knowledge(quote="첫 번째 이미 알아?")),
        ("첫 번째 이미 아는 건 아니야", knowledge(quote="첫 번째 이미 아는 건 아니야")),
        ("첫 번째 이미 알아 라고 말하면?", knowledge()),
        ("첫 번째 몰라", knowledge()),
        ("여기 이미 알아. 전부 보여줘", knowledge("전부", "all", "여기 이미 알아")),
    ],
)
async def test_ambiguous_negated_or_fabricated_correction_never_mutates(query, correction):
    service, searcher = setup()
    old = await new_page(service, await initial(service))
    count = len(searcher.calls)
    result = await chat(service, old, query, feedback="familiarity", familiarity=correction)
    assert result.state.exploration.known == ()
    assert refs(result) == refs(old) and len(searcher.calls) == count


@pytest.mark.parametrize("feedback", ["evaluation", "information_dispute"])
async def test_other_feedback_is_not_knowledge_but_does_not_swallow_explicit_search(feedback):
    service, _ = setup()
    old = await initial(service)
    result = await chat(
        service,
        old,
        "거기 없잖아. 주차 되는 카페 보여줘",
        feedback=feedback,
        search_request_quote="주차 되는 카페 보여줘",
        changes={"parking": "required_true"},
    )
    assert result.state.filters.hard.all[0].value is True
    assert not result.state.exploration.known and not result.state.exploration.excluded
    if feedback == "information_dispute":
        assert "현장과 다를" in render_answer(result.receipt)


async def test_direct_pool_control_preserves_conditions_and_knowledge():
    service, _ = setup()
    old = await new_page(service, await initial(service))
    known = await chat(
        service, old, "첫 번째 이미 알아", feedback="familiarity", familiarity=knowledge()
    )
    result = await service.prepare(
        None,
        PrepareRequest(
            mode="filters",
            previous=known.state,
            remove_filters={"search_pool": "all_places"},
            candidate_pools="v1",
            bookmark_keys=(),
        ),
    )
    assert result.state.filters == known.state.filters and result.state.search_pool == "all_places"
    assert result.state.exploration.known == known.state.exploration.known and "0" in refs(result)


async def test_restore_carries_knowledge_but_explicit_restart_clears_it():
    service, _ = setup()
    old = await initial(service)
    known = await chat(
        service, old, "첫 번째 이미 알아", feedback="familiarity", familiarity=knowledge()
    )
    restored = await service.prepare(
        None,
        PrepareRequest(
            mode="restore",
            restore_filters=known.state.filters,
            restore_pool="new_candidates",
            restore_exploration=known.state.exploration,
            candidate_pools="v1",
            bookmark_keys=(),
        ),
    )
    assert "0" not in refs(restored)
    restarted = await chat(service, restored, "탐색 초기화", browse="restart")
    assert not restarted.state.exploration.known and "0" in refs(restarted)


async def test_old_snapshot_correction_requeries_instead_of_retaining_stale_cards():
    service, searcher = setup()
    now = datetime.now(UTC)
    service.now = lambda: now
    old = await new_page(service, await initial(service))
    service.now = lambda: now + timedelta(seconds=400)
    searcher.rows = searcher.rows[30:]
    changed = await chat(
        service, old, "첫 번째 이미 알아", feedback="familiarity", familiarity=knowledge()
    )
    assert refs(changed) == [str(i) for i in range(30, 46)]


async def test_successive_corrections_do_not_extend_the_lifetime_of_retained_facts():
    service, searcher = setup()
    now = datetime.now(UTC)
    service.now = lambda: now
    old = await new_page(service, await initial(service))
    service.now = lambda: now + timedelta(seconds=250)
    first = await chat(service, old, "첫 번째 이미 알아", familiarity=knowledge())
    assert first.state.snapshot.created_at == old.state.snapshot.created_at
    service.now = lambda: now + timedelta(seconds=350)
    searcher.rows = searcher.rows[30:]
    second = await chat(service, first, "첫 번째 이미 알아", familiarity=knowledge())
    assert refs(second) == [str(i) for i in range(30, 46)]


async def test_page_budget_keeps_a_valid_correction_without_running_another_search():
    from daengs_place.place.contracts import PlaceRef

    service, searcher = setup()
    old = await new_page(service, await initial(service))
    old = old.model_copy(
        update={
            "state": old.state.model_copy(
                update={
                    "exploration": old.state.exploration.model_copy(
                        update={
                            "presented": tuple(
                                PlaceRef(source="kcisa", ref=str(i)) for i in range(1200)
                            )
                        }
                    )
                }
            )
        }
    )
    calls = len(searcher.calls)
    result = await chat(
        service,
        old,
        "첫 번째 이미 알아. 다음 보여줘",
        familiarity=knowledge(),
        search_request_quote="다음 보여줘",
        browse="next",
    )
    assert result.receipt.code == "exploration_budget"
    assert result.state.exploration.known[0].key.ref == "0"
    assert refs(result) == refs(old) and len(searcher.calls) == calls
    assert not result.receipt.result_matches_filters


async def test_revising_a_scope_proposal_keeps_its_pool_and_accepts_against_original_state():
    from tests.place.conversation.test_policy import Interpreter

    service, _ = setup()
    service.planner = Interpreter()
    old = await initial(service)
    pending = await chat(
        service,
        old,
        "새로운 조용한 곳",
        search_scope="new_candidates",
        search_scope_quote="새로운",
        unsupported=("quiet",),
    )
    assert pending.receipt.action == "await_confirmation"
    assert "새 후보" in pending.state.pending_proposal.question
    assert pending.state.search_pool == "all_places"
    service.planner.decision = "revise"
    revised = await chat(service, pending, "주차도 필수", changes={"parking": "required_true"})
    proposal = revised.state.pending_proposal
    assert proposal.pool == "new_candidates" and proposal.base_pool == "all_places"
    assert service.planner.requests[-1].previous.search_pool == "new_candidates"
    service.planner.decision = "accept"
    accepted = await chat(service, revised, "응")
    assert accepted.state.search_pool == "new_candidates"
    assert accepted.state.filters == proposal.candidate


async def test_explicit_exclusion_is_carried_into_saved_search_but_known_is_not():
    service, _ = setup()
    old = await initial(service)
    known = await chat(
        service, old, "첫 번째 이미 알아", feedback="familiarity", familiarity=knowledge()
    )
    excluded = await chat(
        service,
        known,
        "첫 번째 빼줘",
        place_edit={
            "operation": "exclude",
            "operation_quote": "빼줘",
            "targets": [{"kind": "ordinal", "text": "첫 번째"}],
        },
    )
    service.planner.next = Interpretation(
        goal="show", search_scope="bookmarks", search_scope_quote="찜한 곳"
    )
    result = await service.prepare(
        None,
        PrepareRequest(
            mode="chat",
            query="찜한 곳 보여줘",
            previous=excluded.state,
            saved_search="v1",
            candidate_pools="v1",
            bookmark_keys=(),
        ),
    )
    assert result.receipt.saved_search_filters["excluded_keys"] == [
        old.state.snapshot.display_order[0].model_dump()
    ]
    assert result.state.exploration.known == known.state.exploration.known
