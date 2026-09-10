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

# 응급 병원 연락(`vet_contact`)의 문구. 위 리다이렉트와 같은 모듈에 두는 이유도 같다 —
# 같은 상황이 경로에 따라 다른 문장으로 나오면 사용자가 그것을 다른 판정으로 읽는다.
# 첫 줄은 새로 짓지 않고 `SCOPED_REDIRECT_MESSAGES["emergency"]` 를 그대로 쓴다.

#: 전화 우선. 키는 `VetContactPayload.at_night` 이다.
#: 야간 순위 부스트는 이 카드에 없다 — `24h` 태그 개수를 아직 재지 못했고, 그때까지
#: 시간대는 **무엇을 물어볼지**만 바꾼다 (설계 §2-3).
VET_CONTACT_CALL_FIRST: dict[bool, str] = {
    True: "전화로 야간 진료 여부를 먼저 확인하세요.",
    False: "전화로 지금 진료 가능한지 먼저 확인하세요.",
}

#: **조건 없이 나간다.** 후보가 있든 없든 항상 참이고, 가끔만 나오는 고지는 사용자가
#: 기댈 수 없다 (D-051 ⑤ 가 위치 고지에 내린 것과 같은 판단). 이 한 줄이 이 기능의
#: 정직성 전부다 — 인허가 원천에는 진료시간도 응급 여부도 없다.
VET_CONTACT_HOURS_UNKNOWN = (
    "진료 시간과 응급 진료 여부는 공공 데이터에 없어서 확인해 드릴 수 없습니다."
)

#: 좌표가 없을 때. **물음표를 넣지 말 것** — 되묻는 문장으로 읽히면 CLARIFY 를 피한 의미가 없다.
VET_CONTACT_LOCATION_UNKNOWN = "현재 위치를 알 수 없어 가까운 병원을 찾지 못했습니다."

#: Place 의 같은 고지와 **같은 문자열**이다. 두 능력이 위치를 다르게 부르면 안 된다.
VET_CONTACT_CURRENT_LOCATION_FRAME = "현재 기기 위치를 기준으로"

__all__ = [
    "NO_CAPABILITY_MESSAGE",
    "SCOPED_REDIRECT_MESSAGES",
    "VET_CONTACT_CALL_FIRST",
    "VET_CONTACT_CURRENT_LOCATION_FRAME",
    "VET_CONTACT_HOURS_UNKNOWN",
    "VET_CONTACT_LOCATION_UNKNOWN",
    "RefusalReason",
]
