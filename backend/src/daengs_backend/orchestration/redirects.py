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

#: 피부 판정 해설(D-079)의 다음 행동. 모델은 **무엇을** 할지만 고르고 문장은 여기서 나간다 —
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

#: 모델 해설이 병변 이름이나 확률을 말했을 때 **그 문장 대신** 나가는 판정 요약 (D-079).
#: 판정 이름은 `aggregate._SCREENING_VERDICTS` 와 같은 말이다 — 같은 판정이 답변마다 다른
#: 말로 나오면 사용자가 그것을 다른 판정으로 읽는다.
SKIN_VERDICT_SUMMARY: dict[str, str] = {
    "normal": "이번 사진에서는 특이 소견이 보이지 않았어요.",
    "abnormal": "이번 사진에서 이상 소견이 보였어요.",
    "retake": "이번 사진으로는 판정하지 못했어요.",
}

#: 보행 **변화 관찰** 해설(D-080)의 다음 행동. 피부(`SkinAction`)와 셋 다 다르고, 특히
#: **진료 권유가 없다.** 이 서비스는 진단이 아니라 같은 아이의 시간 변화 관찰이고(D-058),
#: 걸음 비교에서 병원을 권하기 시작하면 관찰이 판정으로 되돌아간다. 병원이 필요한 질문
#: ("병원 가야 해?" · "무슨 병이야?")은 행동이 아니라 **거절**로 가고, 그때 나가는
#: `SCOPED_REDIRECT_MESSAGES["diagnosis"]` 의 둘째 문장이 진료를 안내한다.
GaitAction = Literal["same_condition_retake", "keep_observing", "check_conditions"]

GAIT_ACTION_MESSAGES: dict[GaitAction, str] = {
    "same_condition_retake": "같은 거리·같은 각도·비슷한 밝기에서 한 번 더 찍어 비교해 보세요.",
    "keep_observing": "다음에 한 번 더 찍어 흐름을 보면 변화인지 더 분명해져요.",
    "check_conditions": (
        "이번 두 영상은 촬영 조건이 달랐을 수 있어요. 조건을 맞춰 다시 찍으면 차이가 "
        "조건 때문인지 알 수 있어요."
    ),
}

#: 모델 해설이 **방향(좋아졌다·나빠졌다) · 진단어 · 수치**를 말했을 때 그 문장 대신 나가는
#: 변화 요약 (D-080). 앱 `GaitVerdict` 의 네 갈래와 **같은 뜻**이어야 한다 — 같은 비교가
#: 카드와 해설에서 다른 말로 나오면 사용자가 그것을 다른 결과로 읽는다.
GAIT_CHANGE_SUMMARY: dict[str, str] = {
    "no_change": "두 기록을 비교했을 때 주요 관절 움직임은 전반적으로 비슷했어요.",
    "one_side": "한쪽 다리의 여러 관절에서 움직임 차이가 함께 관찰됐어요.",
    "both_sides": (
        "양쪽 다리 모두에서 차이가 관찰됐어요. 걸음 변화보다 촬영 조건이 달랐을 "
        "가능성을 먼저 봐야 해요."
    ),
    "not_enough": "두 영상에서 같은 관절을 충분히 재지 못해 변화가 있었는지 말하기 어려워요.",
}

#: 보행 해설 답 끝에 **조건 없이** 붙는다 (`SKIN_REFERENCE_NOTICE` 와 같은 판단).
#: **진료를 권하지 않는다** — 그것이 이 능력의 행동 집합과 같은 선이다.
GAIT_REFERENCE_NOTICE = (
    "영상으로 본 움직임 비교는 참고용이에요. 실제 변화인지는 같은 조건에서 여러 번 찍어 "
    "봐야 알 수 있어요."
)

#: 두 기록의 분석 버전이 다를 때 **조건 없이** 함께 나간다. 앱도 같은 상황에서 서버
#: `version_warning` 을 띄운다 — 같은 사실이 두 화면에서 같은 무게로 보여야 한다.
GAIT_VERSION_WARNING = (
    "두 기록은 분석 버전이 달라요. 같은 영상이라도 버전이 다르면 움직임 범위가 달라 보일 수 있어요."
)

#: 여섯 판정 지점이 전부 달라졌고 그렇게 볼 근거도 충분할 때 **덧붙는** 한 줄 (D-080).
#: 조건은 `services/gait_context._expert_advisory` 가 정하고, 문장은 여기서 나간다.
#:
#: ⚠️ **"수의사" 가 아니라 "전문가" 다.** 진료 권유는 이 능력의 행동 집합에 없고(그것이
#:    관찰을 판정으로 되돌리는 문이다), 이 줄도 권유가 아니라 **선택지를 알려 주는 말**이다.
#: ⚠️ 정도(심하다 · 악화 · 질환 의심)를 말하지 않는다. 영상만으로는 원인을 알 수 없다는
#:    사실이 이 문장의 앞 절이고, 그것이 이 줄이 과장으로 읽히지 않게 하는 장치다.
GAIT_EXPERT_ADVISORY = (
    "영상만으로는 원인을 알 수 없어요. 이런 변화가 다음에도 반복되면 전문가의 의견을 "
    "받아 보는 것도 좋아요."
)

#: 비교를 해설할 수 없을 때 **이유 범주별로** 나가는 문장 (D-080). 조용히 다른 능력으로
#: 넘기지 않는 이유는 사용자가 비교 화면에서 눌러 들어왔기 때문이다 — general 로 가면 방금
#: 본 비교와 무관한 답이 나오고, HANDOFF 로 가면 "영상을 올려 주세요" 가 다시 나온다.
#: 둘 다 사용자가 한 행동을 부정한다.
GAIT_UNAVAILABLE_MESSAGES: dict[str, str] = {
    "not_found": "비교 정보를 불러올 수 없어요. 기록 화면에서 두 기록을 다시 골라 주세요.",
    "same_record": "같은 기록끼리는 비교할 수 없어요. 다른 날 기록과 비교해 주세요.",
    "different_pet": "서로 다른 아이의 기록은 비교할 수 없어요.",
    "model_mismatch": "두 기록은 분석 방식이 달라서 비교할 수 없어요.",
    "quality": (
        "한쪽 영상에서 분석에 쓸 보행 장면이 부족해 비교하지 못했어요. 밝은 곳에서 강아지 "
        "전신이 보이도록 흔들림 없이 다시 찍어 주세요."
    ),
    "legacy_pair": (
        "예전 분석 방식으로 만든 기록이라 다리별 변화까지는 설명해 드릴 수 없어요. "
        "최근 기록끼리 비교하면 자세히 볼 수 있어요."
    ),
}

__all__ = [
    "DISTANCE_FROM_RECORDED_WALKS_ONLY",
    "GAIT_ACTION_MESSAGES",
    "GAIT_CHANGE_SUMMARY",
    "GAIT_EXPERT_ADVISORY",
    "GAIT_REFERENCE_NOTICE",
    "GAIT_UNAVAILABLE_MESSAGES",
    "GAIT_VERSION_WARNING",
    "NO_CAPABILITY_MESSAGE",
    "SCOPED_REDIRECT_MESSAGES",
    "SKIN_ACTION_MESSAGES",
    "SKIN_REFERENCE_NOTICE",
    "SKIN_VERDICT_SUMMARY",
    "VET_CONTACT_CALL_FIRST",
    "VET_CONTACT_CURRENT_LOCATION_FRAME",
    "VET_CONTACT_HOURS_UNKNOWN",
    "VET_CONTACT_LOCATION_UNKNOWN",
    "GaitAction",
    "RefusalReason",
    "SkinAction",
]
