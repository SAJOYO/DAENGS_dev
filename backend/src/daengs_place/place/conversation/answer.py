"""Answer policy consumes committed execution facts; generation cannot modify them."""

import re

from daengs_place.place.conversation.contract import ConversationAnswer
from daengs_place.place.providers.gemini import GeminiIntentProposerError


def fallback_text(receipt):
    if receipt.execution == "failed":
        return "검색을 완료하지 못했어요. 기존 조건과 결과를 유지했어요."
    if receipt.question:
        return receipt.question
    if receipt.goal == "edit_only":
        return "조건을 변경했어요." if receipt.filters_changed else "이미 적용된 조건이에요."
    if receipt.selected:
        name = receipt.evidence["place"]
        reason = receipt.evidence.get("distance", "")
        return f"{name}을 살펴보세요. {reason}"
    if receipt.returned_count == 0:
        return "현재 조건으로 찾은 장소가 없어요."
    return "현재 조건의 장소를 표시했어요."


async def compose_answer(request, generator=None):
    receipt = request.prepared.receipt
    fallback = ConversationAnswer(
        text=fallback_text(receipt), source="fallback", revision=request.committed_revision
    )
    # Failures/clarifications use the explicit policy instead of embellishing diagnostic text.
    if (
        generator is None
        or receipt.code
        or receipt.goal in {"clarify", "edit_only"}
        or not receipt.evidence
    ):
        return fallback
    try:
        draft = await generator.answer(request)
        if not set(draft.evidence_ids) <= receipt.evidence.keys():
            return fallback
        if receipt.selected and (
            "place" not in draft.evidence_ids or receipt.evidence["place"] not in draft.text
        ):
            return fallback
        known_numbers = set(re.findall(r"\d+(?:\.\d+)?", " ".join(receipt.evidence.values())))
        if not set(re.findall(r"\d+(?:\.\d+)?", draft.text)) <= known_numbers:
            return fallback
        return ConversationAnswer(
            text=draft.text,
            source="llm",
            evidence_ids=draft.evidence_ids,
            revision=request.committed_revision,
        )
    except (GeminiIntentProposerError, ValueError, TimeoutError):
        return fallback
