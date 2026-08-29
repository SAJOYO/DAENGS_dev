"""DAENGS -> 별도 Training RAG FastAPI adapter.

질문 원문은 로그에 남기지 않는다. 외부 서비스의 RAG 내부 계약은 이 모듈에
가두고, router에는 DAENGS가 제공할 공개 응답만 반환한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

from daengs_backend.schemas.training import TrainingChatResponse, TrainingCitation

logger = logging.getLogger(__name__)


class TrainingRagUnavailableError(Exception):
    """외부 RAG의 네트워크·4xx·5xx·계약 오류."""


class TrainingRagTimeoutError(Exception):
    """생성 모델의 read timeout."""


class _UpstreamEvidence(BaseModel):
    rank: int = Field(ge=1)
    heading_path: list[str] = Field(default_factory=list)


class _UpstreamResponse(BaseModel):
    request_id: str
    answer: str
    decision: Literal["ANSWER", "UNCERTAIN", "REFUSE", "MEDICAL_REFUSAL"]
    # 상류가 REFUSE를 내는 이유는 두 가지고 사용자에게 뜻이 다르다. 없으면 빈 문자열로
    # 두어 reason을 아직 안 싣는 상류에서도 계약이 깨지지 않게 한다.
    reason: str = ""
    evidence: list[_UpstreamEvidence] = Field(default_factory=list)


#: 상류 gate의 REFUSE reason -> DAENGS 공개 판정.
#:
#: REFUSE 하나를 전부 UNCERTAIN("현재 자료 범위")으로 접으면 **안전 거절이 자료 부족
#: 으로 표시된다.** 체벌·임의 투약을 거절한 응답에 "지금 자료로는 어렵다"는 라벨이
#: 붙으면, 사용자는 자료가 더 있으면 답해 준다는 뜻으로 읽는다. 상류는 두 경우에 서로
#: 다른 reason과 서로 다른 answer 문구를 이미 보낸다.
_REFUSE_REASON_DECISIONS = {
    "safety_boundary_training_harm": "SAFETY_REFUSAL",
    "safety_boundary_medical": "MEDICAL_REFUSAL",
}


def _citation_label(evidence: _UpstreamEvidence) -> str:
    parts = [part.strip() for part in evidence.heading_path if part.strip()]
    return parts[-1] if parts else f"훈련 근거 {evidence.rank}"


@dataclass(frozen=True)
class TrainingRagClient:
    base_url: str
    connect_timeout_seconds: float
    read_timeout_seconds: float
    transport: httpx.AsyncBaseTransport | None = None

    async def ask(self, *, question: str, trace_id: str) -> TrainingChatResponse:
        timeout = httpx.Timeout(
            connect=self.connect_timeout_seconds,
            read=self.read_timeout_seconds,
            write=self.read_timeout_seconds,
            pool=self.connect_timeout_seconds,
        )
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=timeout,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    "/chat",
                    json={"question": question, "top_k": 4},
                    headers={"X-Request-ID": trace_id},
                )
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            logger.warning("training_rag timeout trace_id=%s", trace_id)
            raise TrainingRagTimeoutError from exc
        except httpx.HTTPError as exc:
            logger.warning("training_rag unavailable trace_id=%s error=%s", trace_id, type(exc).__name__)
            raise TrainingRagUnavailableError from exc

        try:
            upstream = _UpstreamResponse.model_validate(response.json())
        except (ValidationError, ValueError) as exc:
            logger.warning("training_rag invalid_response trace_id=%s", trace_id)
            raise TrainingRagUnavailableError from exc

        # REFUSE는 Training RAG의 내부 gate 상태다. 공개 계약으로 옮길 때 reason을 봐야
        # 한다 — 안전 경계에 막힌 것과 코퍼스에 근거가 없는 것은 사용자에게 다른 사실이다.
        # reason을 못 읽으면(옛 상류) 종전대로 UNCERTAIN이다.
        decision = upstream.decision
        if decision == "REFUSE":
            decision = _REFUSE_REASON_DECISIONS.get(upstream.reason, "UNCERTAIN")
        citations = [
            TrainingCitation(rank=item.rank, label=_citation_label(item))
            for item in upstream.evidence
        ]
        logger.info(
            "training_rag completed trace_id=%s upstream_request_id=%s decision=%s citations=%s",
            trace_id,
            upstream.request_id,
            decision,
            len(citations),
        )
        return TrainingChatResponse(
            decision=decision,
            answer=upstream.answer,
            citations=citations,
        )
