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
    "medication": "어떤 약을 먹일지, 용량, 복용 방법, 부작용은 여기서 안내하지 않아요. 수의사에게 확인해 주세요.",
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

#: 이동 거리를 못 재는 이유 (D-073). **왜 못 하는지까지 말한다** — 댕스는 기록된 산책에서만
#: 거리를 내고, 그것은 지명을 좌표로 바꾸지 않기로 한 결정(D-051 ⑤)의 결과다. 그 결정을
#: 이 한 줄이 사용자에게 갚는다. `VET_CONTACT_HOURS_UNKNOWN` 과 같은 성질이다.
#:
#: **조건은 `GeneralAnswer.unmeasured` 다.** Place · vet_contact 의 고지가 무조건인 것은
#: 능력 자체가 범위여서인데, General 은 catch-all 이라 깎을 범위가 없다. 대신 누락률을
#: `evals/conversation_quality` 가 잰다 — 재지 않는 조건부 고지는 D-051 이 거부한 것이다.
DISTANCE_FROM_RECORDED_WALKS_ONLY = (
    "이동 거리와 시간은 앱에 기록된 산책에서만 계산해요. "
    "말씀해 주신 경로는 기록에 없어서 재어 드릴 수 없어요."
)

#: 피부 판정 해설(D-078)의 다음 행동. 모델은 **무엇을** 할지만 고르고 문장은 여기서 나간다 —
#: "병원에 가 보라" 는 제품 문장이지 생성물이 아니다 (#278). `vet_visit` 은
#: `SCOPED_REDIRECT_MESSAGES["diagnosis"]` 의 둘째 문장과 **같은 말**이다 — 거절로 가든
#: 안내로 가든 병원 권유가 같은 문장으로 읽혀야 한다.
SkinAction = Literal["retake", "vet_visit", "observe"]

SKIN_ACTION_MESSAGES: dict[SkinAction, str] = {
    "retake": "밝은 곳에서 부위가 잘 보이게 가까이 다시 찍어 주세요.",
    "vet_visit": "가까운 동물병원에서 진료를 받아 보세요.",
    "observe": "며칠 지켜보시고 달라 보이면 다시 찍어 확인해 보세요.",
}

#: 피부 해설 답 끝에 **조건 없이** 붙는다. 가끔만 나오는 고지는 기댈 수 없다
#: (`VET_CONTACT_HOURS_UNKNOWN` 과 같은 판단). "진단" 이라는 말은 쓰지 않는다.
SKIN_REFERENCE_NOTICE = "사진으로 본 판정은 참고용이에요. 정확한 확인은 수의사 진료로 해 주세요."

#: 모델 해설이 병변 이름이나 확률을 말했을 때 **그 문장 대신** 나가는 판정 요약 (D-078).
#: 판정 이름은 `aggregate._SCREENING_VERDICTS` 와 같은 말이다 — 같은 판정이 답변마다 다른
#: 말로 나오면 사용자가 그것을 다른 판정으로 읽는다.
SKIN_VERDICT_SUMMARY: dict[str, str] = {
    "normal": "이번 사진에서는 특이 소견이 보이지 않았어요.",
    "abnormal": "이번 사진에서 이상 소견이 보였어요.",
    "retake": "이번 사진으로는 판정하지 못했어요.",
}

__all__ = [
    "DISTANCE_FROM_RECORDED_WALKS_ONLY",
    "NO_CAPABILITY_MESSAGE",
    "SCOPED_REDIRECT_MESSAGES",
    "SKIN_ACTION_MESSAGES",
    "SKIN_REFERENCE_NOTICE",
    "SKIN_VERDICT_SUMMARY",
    "VET_CONTACT_CALL_FIRST",
    "VET_CONTACT_CURRENT_LOCATION_FRAME",
    "VET_CONTACT_HOURS_UNKNOWN",
    "VET_CONTACT_LOCATION_UNKNOWN",
    "RefusalReason",
    "SkinAction",
]
