"""Opt-in real Gemini smoke. Location records are explicitly synthetic; no production DB."""

import os
import re
from pathlib import Path

import pytest
from dotenv import dotenv_values

from daengs_place.place.conversation.answer import compose_answer
from daengs_place.place.conversation.contract import AnswerRequest, PrepareRequest
from daengs_place.place.conversation.service import ConversationService
from daengs_place.place.providers.conversation_gemini import GeminiConversation
from tests.place.support.conversation import Searcher, manual


@pytest.mark.skipif(not os.getenv("DAENGS_CONVERSATION_LIVE_ENV"), reason="explicit live opt-in")
async def test_real_gemini_plans_and_answers_against_synthetic_candidates():
    config = dotenv_values(os.environ["DAENGS_CONVERSATION_LIVE_ENV"])
    key = config.get("GEMINI_API_KEY")
    if not key:
        match = re.search(
            r"(?im)^gemini\s*:\s*(\S+)\s*$",
            Path(os.environ["DAENGS_CONVERSATION_LIVE_ENV"]).read_text(encoding="utf-8-sig"),
        )
        key = match.group(1) if match else None
    assert key, "GEMINI_API_KEY is required (value is never printed)"

    class ObservedModel(GeminiConversation):
        async def answer(self, request):
            try:
                draft = await super().answer(request)
                print("synthetic answer:", draft.model_dump())
                return draft
            except Exception as error:
                print("answer error type:", type(error).__name__)
                raise

    model = ObservedModel(key, config.get("GEMINI_MODEL") or "gemini-3.1-flash-lite")
    searcher = Searcher()
    service = ConversationService(model, searcher=searcher)
    current = await service.prepare(None, manual())
    cases = [
        ("아무 데나 하나 골라줘", "pick_one", "reused", ("shopping", "pet_shop")),
        ("왜 거기를 골랐어?", "explain", "reused", ("shopping", "pet_shop")),
        ("주차되는 곳만 보여줘", "show", "searched", ("shopping", "pet_shop")),
        ("쇼핑 말고 카페로 찾아줘", "show", "searched", ("cafe",)),
    ]
    for revision, (query, goal, execution, kinds) in enumerate(cases, 2):
        current = await service.prepare(
            None,
            PrepareRequest(
                mode="chat",
                query=query,
                previous=current.state,
                visible_order=current.state.snapshot.display_order,
            ),
        )
        assert current.receipt.goal == goal
        assert current.receipt.execution == execution
        assert current.state.filters.candidate_kinds == kinds
        answer = await compose_answer(
            AnswerRequest(query=query, committed_revision=revision, prepared=current), model
        )
        if goal in {"pick_one", "explain"}:
            assert current.receipt.selected is not None
            assert answer.source == "llm"
        print(
            f"goal={goal} execution={execution} searches={len(searcher.calls)} answer={answer.source}"
        )
    assert len(searcher.calls) == 3
