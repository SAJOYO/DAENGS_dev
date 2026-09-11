import pytest

from daengs_place.place.bookmarks import BookmarkFilters
from daengs_place.place.conversation.contract import PrepareRequest
from daengs_place.place.conversation.intent import Interpretation
from daengs_place.place.conversation.saved_search import plan_saved
from daengs_place.place.conversation.search_compilation import compile_search, to_search
from daengs_place.place.conversation.search_policy import resolve_search
from tests.place.conversation.test_bookmark_commands import bookmark, setup


def current():
    return BookmarkFilters(lat=37.5, lng=127, radius_m=3000, kinds=["cafe"], name_query="정원")


@pytest.mark.parametrize("pool", ["all_places", "bookmarks"])
@pytest.mark.parametrize("forbid", [False, True])
def test_no_mention_preserves_scope_and_write_negation_does_not_remove_saved(pool, forbid):
    intent = Interpretation(goal="show", forbid_save=forbid, changes={"parking": "required_true"})
    directive = resolve_search(intent, pool)
    assert directive.pool == pool and not directive.question and not directive.navigation
    source = current() if pool == "bookmarks" else to_search(current())
    result = compile_search(source, intent, directive.pool)
    assert result.hard.all[0].value is True and intent.bookmark is None
    assert result.name_query == "정원"


def test_clearing_scope_and_adding_conditions_prepares_one_search_not_screen_restore():
    before = current()
    intent = Interpretation(
        goal="show", search_scope="all_places", changes={"parking": "required_true"}
    )
    result = plan_saved(before, intent, search_policy="v1")
    assert result.action == "search_places" and result.filters is None
    candidate = result.search_filters
    assert candidate.spatial.radius_m == 3000 and candidate.spatial.lat == before.lat
    assert candidate.candidate_kinds == ("cafe",) and candidate.name_query == before.name_query
    assert candidate.hard.all[0].value is True and not candidate.preferences
    assert before == current()
    assert plan_saved(before, intent).action == "clarify"


def test_scope_only_reuses_current_conditions_instead_of_an_older_search():
    result = plan_saved(
        current(), Interpretation(goal="show", search_scope="all_places"), search_policy="v1"
    )
    assert result.action == "search_places" and result.search_filters == to_search(current())


@pytest.mark.parametrize(
    "filters",
    [
        BookmarkFilters(kinds=["cafe"]),
        BookmarkFilters(lat=37.5, lng=127, kinds=["cafe"]),
        BookmarkFilters(lat=37.5, lng=127, radius_m=3000),
    ],
)
def test_handoff_cannot_invent_origin_radius_or_truncate_categories(filters):
    result = plan_saved(
        filters, Interpretation(goal="show", search_scope="all_places"), search_policy="v1"
    )
    assert result.action == "clarify" and result.search_filters is None


def test_handoff_applies_edits_before_checking_lookup_limits_and_keeps_or_semantics():
    before = BookmarkFilters(lat=37.5, lng=127)
    result = plan_saved(
        before,
        Interpretation(
            goal="show",
            search_scope="all_places",
            changes={
                "radius_m": 5000,
                "kinds": {"operation": "set", "values": ["cafe", "restaurant"]},
                "alternatives": [
                    {"kinds": ["cafe"], "parking": True},
                    {"kinds": ["restaurant"], "exclusive": True},
                ],
            },
        ),
        search_policy="v1",
    )
    assert result.action == "search_places"
    assert result.search_filters.spatial.radius_m == 5000
    assert len(result.search_filters.hard.any) == 2


@pytest.mark.parametrize("pool", ["all_places", "bookmarks"])
def test_navigation_cannot_drop_simultaneous_filter_changes(pool):
    intent = Interpretation(
        goal="show", navigation="restore_search", changes={"parking": "required_true"}
    )
    assert resolve_search(intent, pool).code == "navigation_with_changes"


@pytest.mark.parametrize("pool", ["all_places", "bookmarks"])
def test_s07_overeager_scope_is_not_authorized_by_save_negation(pool):
    intent = Interpretation(
        goal="show",
        forbid_save=True,
        search_scope="all_places",
        search_scope_quote="찜하지 말고",
        changes={"parking": "required_true"},
    )
    directive = resolve_search(intent, pool, "찜하지 말고 주차 되는 카페만 찾아줘")
    assert directive.pool == pool and not directive.question
    result = plan_saved(
        current(), intent, search_policy="v1", query="찜하지 말고 주차 되는 카페만 찾아줘"
    )
    assert result.action == "search" and result.filters.hard.all[0].value is True


def test_forbidden_save_does_not_block_explicit_scope_change_or_accept_invented_quote():
    intent = Interpretation(
        goal="show",
        forbid_save=True,
        search_scope="all_places",
        search_scope_quote="찜 여부 상관없이",
    )
    assert (
        resolve_search(intent, "bookmarks", "찜하지 말고 찜 여부 상관없이 찾아줘").pool
        == "all_places"
    )
    assert resolve_search(intent, "bookmarks", "찜한 곳 중 찾아줘").code == "scope_needs_reference"


def test_short_saved_lookup_request_and_restatement_of_current_scope_do_not_need_clarification():
    intent = Interpretation(goal="show", search_scope="bookmarks", search_scope_quote="찜도 찾아줘")
    assert resolve_search(intent, "all_places", "이 조건으로 찜도 찾아줘").pool == "bookmarks"
    same = Interpretation(goal="show", search_scope="bookmarks")
    assert not resolve_search(same, "bookmarks", "조건 그대로 유지해").question


@pytest.mark.parametrize("pool", ["new_candidates", "unbookmarked"])
def test_future_candidate_modes_cannot_silently_become_all_places(pool):
    intent = Interpretation(goal="show", search_scope=pool, search_scope_quote="새로운 곳")
    assert (
        resolve_search(intent, "all_places", "새로운 곳 찾아줘").code
        == "candidate_pool_unavailable"
    )


async def test_forbidden_save_never_prepares_a_write_even_if_model_outputs_it():
    service, planner, searcher, before = await setup()
    planner.next = bookmark(forbid_save=True)
    result = await service.prepare(
        None,
        PrepareRequest(
            mode="chat",
            query="여기 찜하지 마",
            previous=before,
            bookmark_commands="v1",
            visible_selected=before.snapshot.display_order[0],
        ),
    )
    assert result.receipt.code == "save_forbidden" and result.receipt.bookmark_command is None
    assert result.state.filters == before.filters and result.state.snapshot == before.snapshot
    assert len(searcher.calls) == 1


async def test_ordinary_and_saved_search_apply_the_same_conditions():
    service, planner, _, before = await setup()
    planner.next = Interpretation(
        goal="show",
        forbid_save=True,
        changes={
            "kinds": {"operation": "set", "values": ["cafe"]},
            "parking": "required_true",
        },
    )
    result = await service.prepare(
        None,
        PrepareRequest(
            mode="chat",
            query="찜하지 말고 주차 되는 카페",
            previous=before,
        ),
    )
    saved = compile_search(before.filters, planner.next, "bookmarks")
    assert result.state.filters.hard == saved.hard
    assert result.state.filters.candidate_kinds == tuple(saved.kinds)
    assert result.receipt.bookmark_command is None
