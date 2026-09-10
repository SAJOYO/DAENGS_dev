import pytest

from daengs_place.place.conversation.contract import PrepareRequest
from daengs_place.place.conversation.intent import Interpretation
from daengs_place.place.conversation.service import ConversationService
from tests.place.support.conversation import Planner, Searcher, manual, place


def edit(kind, text, quote="빼줘"):
    return {
        "operation": "exclude",
        "operation_quote": quote,
        "targets": [{"kind": kind, "text": text}],
    }


@pytest.mark.parametrize(
    ("query", "proposal"),
    [
        ("이미 알고 있는 곳이라고", edit("selected", "거기", "이미 알고 있는 곳이라고")),
        ("달나라카페 빼줘", edit("all", "다")),
        ("달나라카페 빼줘", edit("name", "테스트 first")),
        ("달나라카페 빼줘", edit("name", "달나라카페")),
        ("달나라카페 빼줘", edit("selected", "거기")),
        ("여기는 이미 알아", edit("selected", "여기")),
        ("첫 번째 빼지 마", edit("ordinal", "첫 번째", "빼지 마")),
        ("첫 번째 빼줘, 아니 빼지 마", edit("ordinal", "첫 번째")),
        ("첫 번째 제외하지 말고 유지해", edit("ordinal", "첫 번째", "제외하")),
        ("첫 번째 안 빼줘도 돼", edit("ordinal", "첫 번째")),
        ('친구가 "첫 번째 빼줘"라고 했어', edit("ordinal", "첫 번째")),
        ("첫 번째 빼줘라면 어떻게 돼?", edit("ordinal", "첫 번째")),
        ("21번 빼줘", edit("ordinal", "21번")),
    ],
)
async def test_ungrounded_model_proposal_cannot_mutate_or_search(query, proposal):
    searcher = Searcher()
    service = ConversationService(Planner(), searcher=searcher)
    first = await service.prepare(None, manual())
    service.planner.next = Interpretation(goal="show", place_edit=proposal)
    result = await service.prepare(
        None,
        PrepareRequest(
            mode="chat",
            query=query,
            previous=first.state,
            visible_selected=first.state.snapshot.display_order[0],
        ),
    )
    assert result.receipt.code == "invalid_exploration_target"
    assert result.state.exploration == first.state.exploration
    assert result.state.snapshot == first.state.snapshot
    assert result.state.filters == first.state.filters
    assert len(searcher.calls) == 1


@pytest.mark.parametrize(
    ("query", "proposal", "expected"),
    [
        ("테스트 second 빼줘", edit("name", "테스트 second"), "second"),
        ("여기는 빼줘", edit("selected", "여기"), "first"),
        ("두 번째 빼줘", edit("ordinal", "두 번째"), "second"),
        ("2번 빼줘", edit("ordinal", "2번"), "second"),
        ("마지막 빼줘", edit("ordinal", "마지막"), "second"),
    ],
)
async def test_user_reference_resolves_key_on_server(query, proposal, expected):
    searcher = Searcher()
    service = ConversationService(Planner(), searcher=searcher)
    first = await service.prepare(None, manual())
    service.planner.next = Interpretation(goal="show", place_edit=proposal)
    result = await service.prepare(
        None,
        PrepareRequest(
            mode="chat",
            query=query,
            previous=first.state,
            visible_selected=first.state.snapshot.display_order[0],
        ),
    )
    assert [p.key.ref for p in result.receipt.excluded_places] == [expected]
    assert len(searcher.calls) == 2


async def test_duplicate_name_is_not_arbitrarily_resolved():
    searcher = Searcher()
    searcher.rows = [
        place("first").model_copy(update={"name": "같은 이름"}),
        place("second").model_copy(update={"name": "같은 이름"}),
    ]
    service = ConversationService(Planner(), searcher=searcher)
    first = await service.prepare(None, manual())
    service.planner.next = Interpretation(goal="show", place_edit=edit("name", "같은 이름"))
    result = await service.prepare(
        None,
        PrepareRequest(
            mode="chat",
            query="같은 이름 빼줘",
            previous=first.state,
        ),
    )
    assert result.receipt.action == "clarify" and len(searcher.calls) == 1


@pytest.mark.parametrize("query", ["새로고침 해줘", "처음부터 볼래", "초기화하지 마"])
async def test_restart_cannot_silently_clear_exclusions(query):
    service = ConversationService(Planner(), searcher=Searcher())
    first = await service.prepare(None, manual())
    service.planner.next = Interpretation(goal="show", place_edit=edit("name", "테스트 first"))
    excluded = await service.prepare(
        None,
        PrepareRequest(
            mode="chat",
            query="테스트 first 빼줘",
            previous=first.state,
        ),
    )
    service.planner.next = Interpretation(goal="show", browse="restart")
    result = await service.prepare(
        None,
        PrepareRequest(
            mode="chat",
            query=query,
            previous=excluded.state,
        ),
    )
    assert result.receipt.action == "clarify"
    assert result.state.exploration == excluded.state.exploration


@pytest.mark.parametrize(
    ("query", "name", "scope", "remaining"),
    [
        ("테스트 first 빼고 다른 곳 보여줘", "테스트 first", "current", ["second"]),
        ("테스트 first 빼고 더 보여줘", "테스트 first", "next", []),
        ("더카페 빼고 다른 곳 보여줘", "더카페", "current", ["second"]),
    ],
)
async def test_exclusion_does_not_skip_other_displayed_places_without_advance(
    query, name, scope, remaining
):
    searcher = Searcher()
    searcher.rows = [place("first").model_copy(update={"name": name}), place("second")]
    service = ConversationService(Planner(), searcher=searcher)
    first = await service.prepare(None, manual())
    service.planner.next = Interpretation(
        goal="show", browse="next", place_edit=edit("name", name, "빼고")
    )
    result = await service.prepare(
        None, PrepareRequest(mode="chat", query=query, previous=first.state)
    )
    assert result.receipt.browse == scope
    assert [key.ref for key in result.state.snapshot.display_order] == remaining
    assert [p.key.ref for p in result.receipt.excluded_places] == ["first"]
