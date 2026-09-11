import pytest

from daengs_place.place.bookmarks import BookmarkFilters, matches, order_bookmark_hits
from daengs_place.place.conversation.contract import PrepareRequest
from daengs_place.place.conversation.intent import Interpretation, SemanticChanges
from daengs_place.place.conversation.saved_search import compile_saved, plan_saved
from daengs_place.place.search import _hit
from tests.place.conversation.test_bookmark_commands import setup
from tests.place.support.conversation import place


def intent(**kwargs):
    return Interpretation(goal="show", **kwargs)


def test_parking_preference_without_location_keeps_all_hits_and_orders_confirmed_first():
    hits = []
    for i, value in enumerate((False, None, True)):
        p = place(str(i), kind="cafe")
        p.facts.parking = value
        hits.append(_hit(p, None))
    order_bookmark_hits(hits, BookmarkFilters(parking=True))
    assert [h.place.key.ref for h in hits] == ["2", "0", "1"]


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("required_true", [True]),
        ("preferred_true", [True, False, None]),
        ("required_false", [False]),
    ],
)
def test_required_and_preferred_parking_keep_different_unknown_semantics(mode, expected):
    current = BookmarkFilters(lat=37.5, lng=127, radius_m=3000, kinds=["cafe"], name_query="테스트")
    result = plan_saved(current, intent(changes={"parking": mode}))
    assert result.action == "search"
    assert result.filters.radius_m == 3000 and result.filters.name_query == "테스트"
    accepted = []
    for value in (True, False, None):
        p = place("a", kind="cafe", distance=100)
        p.facts.parking = value
        if matches(p, result.filters):
            accepted.append(value)
    assert accepted == expected
    assert result.filters.parking is (mode == "preferred_true")


def test_saved_target_does_not_clear_radius_and_unbounded_preserves_other_conditions():
    current = BookmarkFilters(
        lat=37.5, lng=127, radius_m=3000, kinds=["cafe"], name_query="정원", parking=True
    )
    assert plan_saved(current, intent(search_scope="bookmarks")).filters == current
    after = plan_saved(current, intent(spatial_scope="unbounded")).filters
    assert after.radius_m is None
    assert after.model_dump(exclude={"radius_m"}) == current.model_dump(exclude={"radius_m"})


def test_all_kinds_and_missing_origin_do_not_require_fabricated_coordinates():
    result = plan_saved(BookmarkFilters(), intent(changes={"parking": "preferred_true"}))
    assert result.filters.kinds == [] and result.filters.lat is None and result.filters.parking
    result = plan_saved(
        result.filters, intent(changes={"kinds": {"operation": "set", "values": ["cafe"]}})
    )
    assert result.filters.kinds == ["cafe"]
    assert plan_saved(BookmarkFilters(), intent(changes={"radius_m": 5000})).action == "clarify"


@pytest.mark.parametrize(
    "extra",
    [
        {"unresolved": "ambiguous"},
        {"unsupported": ["quiet"]},
        {"region_query": "제주"},
        {
            "bookmark": {
                "operation": "save",
                "operation_quote": "찜해줘",
                "target": {"kind": "selected", "text": "여기"},
            }
        },
        {
            "place_edit": {
                "operation": "exclude",
                "operation_quote": "빼줘",
                "targets": [{"kind": "selected", "text": "이거"}],
            }
        },
    ],
)
def test_unresolved_unsupported_and_compound_requests_cannot_partially_apply(extra):
    result = plan_saved(BookmarkFilters(), intent(changes={"parking": "required_true"}, **extra))
    assert result.action == "clarify" and result.filters is None


@pytest.mark.parametrize("feedback", ["evaluation", "familiarity", "information_dispute"])
def test_feedback_does_not_apply_overeager_filter_or_scope_edits(feedback):
    result = plan_saved(
        BookmarkFilters(),
        intent(feedback=feedback, search_scope="all_places", changes={"parking": "required_true"}),
    )
    assert result.action == "explain" and result.filters is None


def test_return_scope_keeps_search_conditions_and_compound_return_asks():
    assert (
        plan_saved(BookmarkFilters(), intent(search_scope="all_places")).action == "return_search"
    )
    assert (
        plan_saved(
            BookmarkFilters(),
            intent(search_scope="all_places", changes={"parking": "required_true"}),
        ).action
        == "clarify"
    )


def test_or_branch_meaning_survives_parking_clear_and_contradictions_are_rejected():
    current = compile_saved(
        BookmarkFilters(),
        SemanticChanges(
            kinds={"operation": "set", "values": ["cafe", "restaurant"]},
            alternatives=[
                {"kinds": ["cafe"], "parking": True},
                {"kinds": ["restaurant"], "exclusive": True},
            ],
        ),
        "keep",
    )
    after = compile_saved(current, SemanticChanges(parking="clear"), "keep")
    assert len(after.hard.any) == 2
    assert any(a.capability == "pet_access.exclusive" for b in after.hard.any for a in b.all)
    with pytest.raises(ValueError):
        compile_saved(
            BookmarkFilters(),
            SemanticChanges(parking="required_false", alternatives=[{"parking": True}]),
            "keep",
        )


@pytest.mark.parametrize("capability", [None, "v1"])
async def test_normal_search_prepares_saved_filters_without_consuming_or_replacing_results(
    capability,
):
    service, planner, searcher, before = await setup()
    planner.next = intent(
        search_scope="bookmarks", spatial_scope="unbounded", changes={"parking": "required_true"}
    )
    result = await service.prepare(
        None,
        PrepareRequest(
            mode="chat",
            query="지역 제한 없이 찜한 곳 중 주차 되는 곳만",
            previous=before,
            saved_search=capability,
        ),
    )
    assert result.state.filters == before.filters and result.state.snapshot == before.snapshot
    assert result.state.exploration == before.exploration and len(searcher.calls) == 1
    if capability:
        assert result.receipt.saved_search_filters["radius_m"] is None
        assert result.receipt.saved_search_filters["hard"]["all"][0]["value"] is True
    else:
        assert result.receipt.saved_search_filters is None
