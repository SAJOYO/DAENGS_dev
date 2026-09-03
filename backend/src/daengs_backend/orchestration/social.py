"""Deterministic replies for purely social utterances (greeting / thanks / goodbye).

The semantic router only classifies `social_intent`; it never writes user-facing
prose. These fixed Korean templates are the entire response surface: no model
call, no RoutePlan, no capability execution, no conversation memory. This is
small-talk copy only — it does not define a persona for Training/Life/Walk.
"""

from __future__ import annotations

from daengs_backend.orchestration.contracts import AssistantResponse, AssistantStatus
from daengs_backend.orchestration.semantic import SocialIntent

_SOCIAL_MESSAGES: dict[SocialIntent, str] = {
    "greeting": "안녕하세요! 반려견과 함께 궁금한 점이 있으면 편하게 물어봐 주세요, 댕.",
    "thanks": "저야말로 고마워요! 또 궁금한 게 있으면 편하게 물어봐 주세요, 댕.",
    "goodbye": "다음에 또 만나요! 반려견과 즐거운 시간 보내세요, 댕.",
}


def social_message(intent: SocialIntent) -> str:
    return _SOCIAL_MESSAGES[intent]


def build_social_response(*, request_id: str, intent: SocialIntent) -> AssistantResponse:
    """ANSWERED with the fixed template and nothing else: no results, handoffs, or clarify."""
    return AssistantResponse(
        request_id=request_id,
        status=AssistantStatus.ANSWERED,
        message=social_message(intent),
        results=[],
        handoffs=[],
        clarify=None,
    )


__all__ = ["build_social_response", "social_message"]
