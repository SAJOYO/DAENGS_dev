"""Scoped-redirect copy: fixed user-facing sentences for "no" answers (#278).

Two call sites need the same kind of sentence — one that tells the user what they
*can* ask instead of leaving them with a bare refusal:

- The general-answer fallback's `REFUSED` reasons (`adapters/general.py`) when the
  model itself declines (diagnosis, medication, emergency, institutional, off-topic).
- `aggregate_results`' `FAILED` message (`aggregate.py`) when the plan selected
  nothing at all and there is no handoff — today that is both the general-fallback
  flag being off and any off-topic query the router assigns no capability to, so it
  reuses the same off-topic sentence rather than a separate "no feature" sentence.

These are product sentences, not model output — a model never writes them (see
`general.py`'s module docstring for why). Keeping them in one module is what keeps a
decline reading the same regardless of which path produced it.
"""

from __future__ import annotations

from typing import Literal

RefusalReason = Literal["diagnosis", "medication", "emergency", "institutional", "off_topic"]

SCOPED_REDIRECT_MESSAGES: dict[RefusalReason, str] = {
    "diagnosis": "증상의 원인이나 병명은 여기서 판단하지 않아요. 가까운 동물병원에서 진료를 받아 보세요.",
    "medication": "약이나 영양제, 용량은 여기서 안내하지 않아요. 수의사에게 확인해 주세요.",
    "emergency": "응급 상황으로 보여요. 지금 바로 동물병원으로 가세요.",
    "institutional": (
        "제도·법령·요금·기한 같은 사실은 근거와 함께 답하는 제도 정보 기능에 물어봐 주세요."
    ),
    "off_topic": "반려견에 관한 질문만 도와드릴 수 있어요.",
}

# `aggregate.py` 의 빈 선택 FAILED 경로가 쓰는 이름. off_topic 과 같은 문장이다 —
# 라우터가 아무것도 못 고른 요청은 실무상 대부분 반려견과 무관한 요청이었다.
NO_CAPABILITY_MESSAGE = SCOPED_REDIRECT_MESSAGES["off_topic"]

__all__ = ["NO_CAPABILITY_MESSAGE", "SCOPED_REDIRECT_MESSAGES", "RefusalReason"]
