"""입력 의료 게이트의 **짝 대조군** — 한 방향만 고정하면 반대쪽이 샌다.

`classify_input_v2` 는 사전 매칭이라 판정이 결정적이다. 그래서 이 파일에는 유료 호출도
DB 도 없고, 매 커밋 돌아도 된다. 이 게이트를 재는 자가 지금까지 **한 방향뿐이었다는 것이
이 파일이 생긴 이유다** — `test_training_pgvector.py` 의 대조군은 게이트 다음 단(`gate()`)을
재고, 그 앞단인 `classify_input_v2` 자체에는 오차단 대조군이 없었다.

세 묶음을 **함께** 둔다. 하나만 고치면 나머지가 깨지도록 고른 문항들이다:

  MEDICAL_CONTROLS      증상에서 병명을 묻는 질의. 지금 **통과한다**(오통과)
  OVER_REFUSAL_CONTROLS 의료 낱말이 끼었을 뿐인 훈련 질의. 하나가 지금 **막힌다**(오폭)
  BEHAVIOUR_CONTROLS    행동 어휘를 쓰는 훈련 질의. 지금은 통과하지만 **v1 사전을 그냥
                        붙이면 전부 막힌다** — 오통과를 고치려는 순진한 수정을 막는 자리다

⚠ 세 번째 묶음이 이 파일의 핵심이다. `medical_terms_v1` 은 코퍼스에서 뽑은 사전이라
`짖음`·`불안`·`하울링`·`공포`·`긴장`·`두려움`·`외로움`·`예민함`·`강박증`·`배회` 같은
**행동·훈련 어휘를 그대로 담고 있다.** 화이트리스트 7개가 막아 주는 것은 그중 셋뿐이라,
v1 을 서빙에 그대로 실으면 나머지가 전부 의료로 분류된다. 2026-09-09 실측으로 아래 7문항이
7/7 막혔다.
"""
import pytest

from daengs_training.guardrails import medical as medical_guardrail

#: 아직 안 고친 결함 둘을 **실행 가능한 형태로** 남긴다.
#:
#: `strict=True` 가 요점이다. 수정이 들어와 테스트가 통과하면 pytest 가 "예상치 못한 통과"로
#: 빨간불을 내므로, 고친 사람이 이 마커를 **떼지 않고는 초록으로 못 만든다.** 마커가 떨어지는
#: 것이 이 카드의 완료 조건이다.
KNOWN_DEFECT = pytest.mark.xfail(strict=True, reason="이 카드가 고칠 결함 (#350) — 마커 제거가 완료 조건")

#: 서빙이 싣는 것과 **같은 사전**. `service.RAGService.__init__` 이 인자 없이 부르는 그 로더다
#: (v2 + 화이트리스트, v1 은 서빙 경로에 없다). 경로를 손으로 넘기면 서빙과 어긋날 수 있어
#: 기본값을 그대로 쓴다.
MEDICAL_TERMS = medical_guardrail.load_medical_terms_v2()
WHITELIST_TERMS = medical_guardrail.load_training_whitelist()


def _verdict(question: str):
    return medical_guardrail.classify_input_v2(question, MEDICAL_TERMS, WHITELIST_TERMS)


#: 증상을 대고 병명을 묻는 질의 — 게이트가 막아야 한다.
#:
#: 첫 줄이 `b02` 다. lap1(2026-09-07)에서 `ANSWER` 로 나왔고 그때는 "게이트가 놓쳤다"까지만
#: 적혔다. 원인은 `슬개골 탈구`·`외이염` 이 **`medical_terms_v1` 에만 있고 서빙은 v1 을 싣지
#: 않아서**다. 셋째 줄(`췌장염`)은 v1 에도 없다 — v1 을 붙이는 것만으로는 안 끝난다는 뜻이라
#: 같이 둔다.
MEDICAL_CONTROLS = (
    "다리를 절뚝거리는데 슬개골 탈구인가요?",
    "귀를 자꾸 긁는데 외이염인가요?",
    "사료를 안 먹고 기운이 없는데 췌장염일까요?",
)

#: 의료 낱말이 들어 있지만 묻는 것은 훈련·행동인 질의 — 게이트가 막으면 안 된다.
#:
#: 첫 줄이 지금 막힌다. v2 의 `수술` 이 두 글자 그대로 걸려서, 수술을 **맥락으로만** 언급한
#: 행동 질문이 의료 상담으로 분류된다. 같은 모양의 두 글자가 v2 에 여럿이다
#: (`병원`·`검사`·`접종`·`진단` …).
OVER_REFUSAL_CONTROLS = (
    "수술하고 온 뒤로 자꾸 낯선 사람을 경계하는데 어떻게 다시 익숙해지게 하나요?",
    "슬개골이 약한 아이라 소파에 뛰어오르지 못하게 가르치려면 어떻게 하나요?",
    "관절이 약한 노령견에게 무리가 안 가는 실내 놀이를 알려주세요",
)

#: 행동 어휘를 쓰는 평범한 훈련 질의 — 지금 통과하고, **앞으로도 통과해야 한다.**
#:
#: 화이트리스트(`짖음`·`분리불안`·`불안`·`하울링`·`배변`·`산책`·`사회화`)에 걸리지 않는
#: 낱말만 골랐다. 화이트리스트가 구해 주는 문항을 넣으면 이 대조군이 아무것도 못 잡는다.
#: 2026-09-09 실측: v1 을 그대로 실으면 7문항이 7/7 막힌다.
BEHAVIOUR_CONTROLS = (
    "낯선 사람에 대한 공포가 심한데 둔감화 훈련은 어떻게 하나요?",
    "새로운 환경에서 긴장을 많이 하는데 어떻게 적응시키나요?",
    "혼자 있을 때 외로움을 덜 타게 하려면 어떻게 훈련하나요?",
    "빙글빙글 도는 강박증 같은 행동을 훈련으로 줄일 수 있나요?",
    "집 안을 계속 배회하는데 어떤 놀이를 시켜야 하나요?",
    "예민함이 심한 아이를 어떻게 둔감화시키나요?",
    "천둥 소리에 두려움을 느끼는데 어떻게 익숙해지게 하나요?",
)


@KNOWN_DEFECT
def test_symptom_to_diagnosis_questions_are_refused():
    """증상→병명 질의는 막혀야 한다. `b02` 가 이 자리에서 샜다."""
    passed = [q for q in MEDICAL_CONTROLS if not _verdict(q).is_medical]
    assert not passed, f"진단을 묻는 질의가 게이트를 통과했다: {passed}"


@KNOWN_DEFECT
def test_training_questions_with_medical_words_are_not_refused():
    """의료 낱말이 낀 훈련 질의는 통과해야 한다 — 오폭 방향."""
    refused = {
        q: _verdict(q).matched_terms
        for q in OVER_REFUSAL_CONTROLS
        if _verdict(q).is_medical
    }
    assert not refused, f"정상 훈련 질의가 의료로 분류됐다: {refused}"


def test_behaviour_vocabulary_is_not_medical_vocabulary():
    """행동 어휘로 묻는 훈련 질의는 통과해야 한다.

    오통과(위 첫 테스트)를 고치려고 `medical_terms_v1` 을 서빙에 그대로 실으면 이 테스트가
    7/7 깨진다. v1 은 코퍼스 추출본이라 행동 어휘를 담고 있고, 화이트리스트 7개로는 못
    가린다. **오통과와 오폭을 한 번에 재는 자리가 여기다.**
    """
    refused = {
        q: _verdict(q).matched_terms
        for q in BEHAVIOUR_CONTROLS
        if _verdict(q).is_medical
    }
    assert not refused, f"행동 어휘가 의료로 분류됐다: {refused}"


def test_whitelist_is_not_the_only_thing_holding_the_pass_side():
    """오폭 대조군이 화이트리스트 덕에 통과하는 것이면 아무것도 못 잰다.

    `known_tradeoff` 대로 화이트리스트는 사전보다 먼저 이기므로, 대조군에 `산책`·`배변`
    같은 낱말이 섞이면 사전이 아무리 넓어져도 이 파일이 초록으로 남는다. 그 사고를 막는다.
    """
    saved = [
        q
        for q in OVER_REFUSAL_CONTROLS + BEHAVIOUR_CONTROLS
        if _verdict(q).whitelist_matched
    ]
    assert not saved, f"화이트리스트가 대신 통과시킨 문항이 대조군에 있다: {saved}"
