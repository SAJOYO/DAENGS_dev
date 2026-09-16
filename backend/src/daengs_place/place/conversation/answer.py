"""Answers are rendered from committed facts; arbitrary prose has no authority."""

from daengs_place.place.conversation.contract import ConversationAnswer
from daengs_place.place.conversation.render import render_answer

# Kept as an import alias for the offline research replay of old receipts.
fallback_text = render_answer


async def compose_answer(request, generator=None):
    receipt = request.prepared.receipt
    return ConversationAnswer(
        text=render_answer(receipt, request.prepared.state.filters),
        source="fallback",
        revision=request.committed_revision,
        evidence_ids=tuple(
            dict.fromkeys(
                (
                    *(("place",) if receipt.selected and "place" in receipt.evidence else ()),
                    *(fact.attribute for fact in receipt.facts),
                    *(("selection_reason",) if receipt.selection_basis else ()),
                )
            )
        ),
    )
