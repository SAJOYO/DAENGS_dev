import pytest

from daengs_place.place.conversation.contract import PrepareRequest
from daengs_place.place.conversation.intent import Interpretation
from daengs_place.place.conversation.render import render_answer
from daengs_place.place.conversation.service import ConversationService
from tests.place.support.conversation import Planner, Searcher, manual, place


async def setup():
    search = Searcher()
    search.rows = [
        place(p.key.ref, kind=p.match.kind, distance=p.distance_m, source="kcisa")
        for p in search.rows
    ]
    planner = Planner()
    service = ConversationService(planner, searcher=search)
    before = (await service.prepare(None, manual())).state
    return service, planner, search, before


def bookmark(operation="save", text="여기", kind="selected", quote="찜해줘", **extra):
    return Interpretation(
        goal="edit_only",
        bookmark={
            "operation": operation,
            "operation_quote": quote,
            "target": {"kind": kind, "text": text},
        },
        **extra,
    )


@pytest.mark.parametrize(
    ("query", "intent", "saved"),
    [
        ("여기 찜해줘", bookmark(), True),
        ("여기 찜 해제해줘", bookmark("remove", quote="찜 해제해줘"), False),
        ("여기 남겨둬", bookmark(quote="남겨둬"), True),
        ("두 번째 찜해줘", bookmark(text="두 번째", kind="ordinal"), True),
        ("'테스트 first'만 찜해줘", bookmark(text="테스트 first", kind="name"), True),
    ],
)
async def test_command_preserves_search_and_has_no_write_or_answer_authority(query, intent, saved):
    service, planner, search, before = await setup()
    planner.next = intent
    selected = before.snapshot.display_order[0]
    result = await service.prepare(
        None,
        PrepareRequest(
            mode="chat",
            query=query,
            previous=before,
            bookmark_commands="v1",
            visible_selected=selected,
            visible_order=before.snapshot.display_order,
        ),
    )
    assert result.receipt.bookmark_command.saved is saved
    assert (
        result.receipt.bookmark_command.key
        == before.snapshot.display_order[1 if intent.bookmark.target.kind == "ordinal" else 0]
    )
    assert result.state.filters == before.filters
    assert result.state.snapshot == before.snapshot
    assert result.state.exploration == before.exploration
    assert result.state.selected == selected
    assert len(search.calls) == 1
    assert result.receipt.execution == "not_run"
    assert "확인해 주세요" in render_answer(result.receipt)


@pytest.mark.parametrize(
    "query",
    [
        "여기 괜찮네",
        "여기 이미 아는 곳이야",
        "여기 없잖아 씨발",
        "여기 찜하지 말고 다른 곳 보여줘",
        "여기 찜해줘라고 말했어",
        "여기 찜해줘? 아니 하지 마",
        "여기 찜해줘 그리고 다음 보여줘",
        '"여기 찜해줘"',
        "여기 찜해줘라면 어떻게 해?",
    ],
)
async def test_overeager_model_cannot_turn_feedback_negation_or_composition_into_save(query):
    service, planner, search, before = await setup()
    planner.next = bookmark()
    result = await service.prepare(
        None,
        PrepareRequest(
            mode="chat",
            query=query,
            previous=before,
            bookmark_commands="v1",
            visible_selected=before.snapshot.display_order[0],
        ),
    )
    assert result.receipt.bookmark_command is None
    assert result.state.snapshot == before.snapshot
    assert result.state.filters == before.filters
    assert result.state.exploration == before.exploration
    assert len(search.calls) == 1


async def test_old_client_gets_guidance_and_ambiguous_target_never_executes():
    service, planner, _, before = await setup()
    planner.next = bookmark()
    request = PrepareRequest(mode="chat", query="여기 찜해줘", previous=before)
    assert (await service.prepare(None, request)).receipt.code == "bookmark_client_required"
    request = request.model_copy(update={"bookmark_commands": "v1"})
    assert (await service.prepare(None, request)).receipt.bookmark_command is None


async def test_feedback_cannot_smuggle_filter_exclusion_or_bookmark_mutations():
    service, planner, search, before = await setup()
    planner.next = Interpretation(
        goal="show",
        feedback="information_dispute",
        changes={"kinds": {"operation": "set", "values": ["cafe"]}},
        place_edit={
            "operation": "exclude",
            "operation_quote": "빼줘",
            "targets": [{"kind": "all", "text": "전체"}],
        },
    )
    selected = before.snapshot.display_order[1]
    result = await service.prepare(
        None,
        PrepareRequest(
            mode="chat",
            query="거기 없잖아 씨발",
            previous=before,
            bookmark_commands="v1",
            visible_selected=selected,
        ),
    )
    assert result.receipt.code == "feedback_no_mutation"
    assert result.state.selected == selected
    assert result.state.snapshot == before.snapshot
    assert result.state.filters == before.filters
    assert result.state.exploration == before.exploration
    assert len(search.calls) == 1
    assert "확인할 수 없어요" in render_answer(result.receipt)


async def test_negated_bookmark_with_explicit_next_advances_only_search():
    service, planner, _, before = await setup()
    planner.next = Interpretation(goal="show", browse="next")
    result = await service.prepare(
        None,
        PrepareRequest(
            mode="chat",
            query="여기 찜하지 말고 다른 곳 보여줘",
            previous=before,
            bookmark_commands="v1",
        ),
    )
    assert result.receipt.bookmark_command is None
    assert result.receipt.browse == "next"
    assert result.state.filters == before.filters
    assert result.state.exploration.excluded == before.exploration.excluded


@pytest.mark.parametrize(
    ("query", "intent"),
    [
        ("여기 남겨둬", bookmark(quote="여기 남겨둬")),
        (
            "'테스트 first'만 찜해줘",
            bookmark(text="테스트 first", kind="name", quote="'테스트 first'만 찜해줘"),
        ),
    ],
)
async def test_operation_evidence_can_quote_the_complete_positive_request(query, intent):
    service, planner, _, before = await setup()
    planner.next = intent
    result = await service.prepare(
        None,
        PrepareRequest(
            mode="chat",
            query=query,
            previous=before,
            bookmark_commands="v1",
            visible_selected=before.snapshot.display_order[0],
        ),
    )
    assert result.receipt.bookmark_command.saved
