import pytest

from daengs_place.place.conversation.contract import ExecutionReceipt
from daengs_place.place.conversation.render import render_answer
from daengs_place.place.conversation.scope import PROCESSING_FAILED
from tests.place.conversation.test_policy import chat, offer


@pytest.mark.parametrize(
    "question", ["원하는 장소나 바꿀 조건을 짧게 알려주세요.", "receipt 오류", ""]
)
def test_invalid_proposal_never_turns_into_a_missing_input_question(question):
    receipt = ExecutionReceipt(
        goal="clarify",
        execution="not_run",
        code="invalid_plan",
        question=question,
        result_matches_filters=True,
        returned_count=2,
    )
    assert render_answer(receipt) == PROCESSING_FAILED
    assert receipt.execution == "not_run" and not receipt.filters_changed


def test_internal_place_label_is_not_printed_and_selection_is_still_reported():
    receipt = ExecutionReceipt(
        goal="pick_one",
        execution="reused",
        result_matches_filters=True,
        returned_count=1,
        selected={"source": "tourapi", "ref": "1"},
        evidence={"place": "revision=5 session_id=secret"},
    )
    assert render_answer(receipt) == "한 곳 골라뒀어요!"
    # Output filtering cannot alter the selected identity or receipt.
    assert receipt.selected.ref == "1" and receipt.evidence["place"].endswith("secret")


def test_failed_search_cannot_be_announced_as_a_success():
    receipt = ExecutionReceipt(
        goal="pick_one",
        execution="failed",
        result_matches_filters=False,
        returned_count=1,
        selected={"source": "tourapi", "ref": "1"},
        evidence={"place": "골라뒀어요!"},
    )
    answer = render_answer(receipt)
    assert "못했어요" in answer and "그대로" in answer and "골라뒀어요" not in answer


async def test_hidden_or_overlong_confirmation_is_cleared_before_bare_consent():
    service, _, searcher, initial, pending = await offer()
    for question in ("session_id를 적용할까요?", "조건" * 180 + "으로 찾을까요?"):
        unsafe = pending.state.model_copy(
            update={
                "pending_proposal": pending.state.pending_proposal.model_copy(
                    update={"question": question}
                )
            }
        )
        answer = await chat(service, unsafe, "응")
        assert answer.receipt.action == "clarify"
        assert answer.state.pending_proposal is None
        assert answer.state.filters == initial.state.filters
        assert len(searcher.calls) == 1
        assert render_answer(answer.receipt) == "조건을 짧게 나눠서 알려주세요."


def test_dispute_and_long_explanation_keep_the_two_sentence_budget():
    receipt = ExecutionReceipt(
        goal="explain",
        execution="reused",
        result_matches_filters=True,
        feedback="information_dispute",
        returned_count=1,
        selected={"source": "tourapi", "ref": "1"},
        evidence={"place": "카페"},
        facts=(
            {"attribute": "parking", "status": "known", "value": True},
            {"attribute": "pet_allowed", "status": "unknown"},
        ),
    )
    text = render_answer(receipt)
    assert "현장과 다를" in text and len(text.split(". ")) <= 2
