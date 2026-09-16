import asyncio
import copy

import pytest

from daengs_backend.services.facility_tools.dialogue import FacilityDialogue, ToolTurn, valid_answer
from daengs_place.place.commands.view import references


def function(name, arguments, call_id="call-1"):
    return {
        "status": "requires_action",
        "steps": [
            {"type": "thought", "signature": "opaque-provider-signature"},
            {"type": "function_call", "name": name, "arguments": arguments, "id": call_id},
        ],
    }


def answer(text):
    return {
        "status": "completed",
        "steps": [{"type": "model_output", "content": [{"type": "text", "text": text}]}],
    }


class Provider:
    def __init__(self, responses):
        self.responses, self.calls = iter(responses), []

    async def _call(self, payload):
        self.calls.append(copy.deepcopy(payload))
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value


@pytest.mark.parametrize(
    "text",
    [
        "멍멍! 주차 가능한 카페는 '평가 장소 A' 한 곳을 찾았어요, 같이 가볼까요?",
        "멍멍! 주차 가능한 카페는 100m 거리에 있는 '평가 장소 A' 한 곳뿐이에요, 같이 가볼까요?",
        "주차 가능한 카페 한 곳 찾았어멍 🐾",
        "천만에요, 우리 댕댕이와 함께 즐거운 시간 보내시길 바랄게요! 멍멍!",
    ],
)
def test_short_mascot_answers_are_valid(text):
    assert valid_answer(text)


@pytest.mark.parametrize("text", ["한 줄\n다른 줄", "한 줄\r다른 줄", "가" * 71, ""])
def test_long_or_multiline_answers_are_invalid(text):
    assert not valid_answer(text)


async def test_native_tool_result_then_free_answer(workspace):
    provider = Provider(
        [
            function(
                "search_places",
                {"category": {"operation": "set", "values": ["cafe"]}, "parking": "required"},
            ),
            answer("주차 가능한 카페 한 곳 찾았어멍 🐾"),
        ]
    )
    turn = ToolTurn("turn-1", "주차되는 카페만 보여줘", workspace.state.revision)
    await FacilityDialogue(provider).run(turn, workspace)
    assert turn.status == "ready"
    assert len(references(workspace.state)) == 1
    history = provider.calls[1]["input"]
    assert history[1] == {"type": "thought", "signature": "opaque-provider-signature"}
    assert history[-1]["type"] == "function_result"
    assert history[-1]["call_id"] == "call-1"
    assert "평가 장소 A" in history[-1]["result"][0]["text"]


async def test_thanks_needs_no_tool_or_state_change(workspace):
    before = workspace.state
    turn = ToolTurn("thanks", "고마워", before.revision)
    await FacilityDialogue(Provider([answer("언제든 같이 찾아보자멍 🐾")])).run(turn, workspace)
    assert turn.status == "ready" and not turn.executions
    assert workspace.state == before


async def test_answer_failure_resume_does_not_repeat_search(workspace):
    provider = Provider(
        [
            function("search_places", {"parking": "required"}),
            TimeoutError(),
            answer("주차 가능한 두 곳을 찾았어 🐾"),
        ]
    )
    turn = ToolTurn("resume", "주차되는 곳", workspace.state.revision)
    service = FacilityDialogue(provider)
    await service.run(turn, workspace)
    assert turn.status == "provider_error"
    calls = len(workspace.commands.searcher.calls)
    revision = workspace.state.revision
    await service.run(turn, workspace)
    assert turn.status == "ready" and workspace.state.revision == revision
    assert len(workspace.commands.searcher.calls) == calls


async def test_cancel_during_command_resumes_without_duplicate_execution(workspace):
    started, release = asyncio.Event(), asyncio.Event()
    searcher = workspace.commands.searcher

    async def delayed(*args, **kwargs):
        started.set()
        await release.wait()
        return await searcher(*args, **kwargs)

    workspace.commands.searcher = delayed
    provider = Provider(
        [
            function("search_places", {"parking": "required"}),
            answer("주차 가능한 두 곳을 찾았어멍 🐾"),
        ]
    )
    turn = ToolTurn("cancelled", "주차되는 곳", workspace.state.revision)
    service = FacilityDialogue(provider)
    running = asyncio.create_task(service.run(turn, workspace))
    await started.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    release.set()
    await workspace.records["cancelled:0"][1]
    calls, revision = len(searcher.calls), workspace.state.revision
    await service.run(turn, workspace)
    assert turn.status == "ready" and len(turn.executions) == 1
    assert len(searcher.calls) == calls and workspace.state.revision == revision


async def test_format_retry_has_no_action_tools(workspace):
    provider = Provider(
        [
            function("search_places", {"parking": "required"}),
            answer("긴 설명입니다. " * 20),
            answer("주차 가능한 두 곳이야멍 🐾"),
        ]
    )
    turn = ToolTurn("format", "주차되는 곳", workspace.state.revision)
    await FacilityDialogue(provider).run(turn, workspace)
    assert turn.status == "ready" and len(turn.executions) == 1
    assert "tools" not in provider.calls[-1]


async def test_new_proposal_cannot_accept_itself_in_same_turn(workspace):
    before = workspace.state
    provider = Provider(
        [
            function("search_places", {"parking": "required", "unavailable": ["조용함"]}),
            function("resolve_search_proposal", {"accept": True}, "self-consent"),
        ]
    )
    turn = ToolTurn("proposal", "조용하고 주차되는 곳", before.revision)
    await FacilityDialogue(provider).run(turn, workspace)
    assert turn.status == "provider_protocol_error"
    assert workspace.state.proposal is not None
    assert workspace.state.filters == before.filters
    assert workspace.state.result == before.result
    assert len(turn.executions) == 1
    assert "tools" not in provider.calls[1]


async def test_proposal_blocks_remaining_calls_in_same_batch(workspace):
    before = workspace.state
    response = function("search_places", {"parking": "required", "unavailable": ["조용함"]})
    response["steps"].append(
        {
            "type": "function_call",
            "name": "search_places",
            "arguments": {"parking": "clear"},
            "id": "bypass",
        }
    )
    provider = Provider(
        [response, answer("조용함은 확인이 어려워서 이 조건으로 찾아볼지 알려줘멍 🐾")]
    )
    turn = ToolTurn("batch-proposal", "조용하고 주차되는 곳", before.revision)
    await FacilityDialogue(provider).run(turn, workspace)
    assert turn.status == "ready"
    assert workspace.state.proposal is not None and workspace.state.filters == before.filters
    assert turn.executions[1]["result"]["code"] == "awaiting_user_confirmation"


async def test_stale_view_never_runs_model(workspace):
    turn = ToolTurn("stale", "두 번째 선택", workspace.state.revision - 1)
    provider = Provider([])
    await FacilityDialogue(provider).run(turn, workspace)
    assert turn.status == "conflict" and not provider.calls


async def test_tool_rounds_stop_at_budget(workspace):
    provider = Provider([function("search_places", {}, f"call-{i}") for i in range(8)])
    turn = ToolTurn("loop", "계속 찾아", workspace.state.revision)
    await FacilityDialogue(provider, max_calls=2).run(turn, workspace)
    assert turn.status == "limit_reached" and len(provider.calls) == 2


async def test_invalid_tool_cannot_run_arbitrary_command(workspace):
    before = workspace.state
    provider = Provider([function("set_bookmark", {"place_ref": "invented", "bookmarked": True})])
    turn = ToolTurn("unavailable", "찜해줘", before.revision)
    await FacilityDialogue(provider).run(turn, workspace)
    assert turn.status == "provider_protocol_error" and workspace.state == before


async def test_invalid_arguments_return_error_to_model_without_changes(workspace):
    before = workspace.state
    provider = Provider(
        [function("search_places", {"radius_m": -1}), answer("그 범위로는 찾을 수 없어멍 🐾")]
    )
    turn = ToolTurn("invalid", "반경 바꿔", before.revision)
    await FacilityDialogue(provider).run(turn, workspace)
    assert turn.status == "ready" and workspace.state == before
    assert provider.calls[1]["input"][-1]["is_error"] is True
