"""훈련 RAG gateway의 공개 요청·응답 DTO.

외부 Training RAG의 내부 점수, 모델명, gate 사유는 여기로 넘기지 않는다.
DAENGS 클라이언트에는 답변 상태와 현재 API가 실제로 준 인용 정보만 제공한다.
"""

from typing import Literal

from pydantic import BaseModel, Field


TrainingDecision = Literal["ANSWER", "UNCERTAIN", "SAFETY_REFUSAL", "MEDICAL_REFUSAL"]


class TrainingChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class TrainingCitation(BaseModel):
    """사용자에게 표시 가능한 근거 카드.

    chunk ID와 similarity score는 내부 식별자·디버그 값이므로 노출하지 않는다.
    """

    rank: int = Field(ge=1)
    label: str = Field(min_length=1, max_length=500)


class TrainingChatResponse(BaseModel):
    decision: TrainingDecision
    answer: str
    citations: list[TrainingCitation]
