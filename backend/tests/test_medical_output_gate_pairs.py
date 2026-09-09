"""출력 의료 게이트의 **짝 대조군** — 입력단(`test_medical_gate_pairs.py`)의 짝이다.

`classify_output_v2` 는 *병명·증상 낱말* 과 *처방 마커* 가 **같이** 나올 때만 차단한다. 그
설계는 독스트링에 적혀 있지만, 그 모듈은 스스로 이렇게 자백해 두었다:

    this has not been measured against a corpus of real generated answers …
    not scored for a false-positive rate

실제로 재 보니 그 자백이 맞다. lap1 의 진짜 답변 24건 중 **전이 용어가 등장한 것은 1건**
(`b02`)뿐이고, 그마저 원래도 `수의사`·`병원`·`진단` 으로 걸려 있어 판정이 안 바뀐다. 즉
**실제 랩으로는 이 게이트의 오폭률을 못 잰다.** 그래서 여기 손으로 짠 짝을 둔다.

⚠ 랩 답변으로 이 게이트를 재려 하지 마라. 위 이유로 분모가 1이다.
"""
from daengs_training.generation.gemini import MEDICAL_REFUSAL_TEMPLATE
from daengs_training.guardrails import medical as medical_guardrail

#: 서빙과 같은 사전 (v1 선별본 + v2). `service.RAGService` 가 출력단에도 **같은 집합**을 넘긴다
#: — `apply_output_guardrail(raw_answer, self.medical_terms, self.whitelist_terms)`.
#: 입력 사전을 넓히면 출력 차단도 같이 넓어진다는 뜻이라, 사전을 만질 때 이 파일도 같이 봐야 한다.
MEDICAL_TERMS = medical_guardrail.load_serving_medical_terms()
WHITELIST_TERMS = medical_guardrail.load_training_whitelist()


def _verdict(answer):
    return medical_guardrail.apply_output_guardrail(answer, MEDICAL_TERMS, WHITELIST_TERMS)


#: 병명 + 처방 마커가 같이 있는 답변 — 차단해야 한다.
#:
#: `관절염`·`외이염` 은 2026-09-09 에 v1 선별본으로 전이한 낱말이다. 전이가 입력단만이 아니라
#: **출력단에서도 실제로 동작하는지**를 여기서 확인한다.
BLOCK_CONTROLS = (
    "관절염이 의심되면 소염제를 처방받아 하루 두 번 복용시키세요.",
    "외이염이 보이면 연고를 하루 2회 발라 주고 항생제를 5일간 투약합니다.",
)

#: 임상 낱말이 있어도 처방이 아닌 훈련 조언 — 차단하면 안 된다.
#:
#: 둘째 줄은 전이 용어(`절뚝거림`)를 담고 있어 **디스클레이머는 붙는다.** 그건 설계대로이고
#: 차단이 아니다. 이 대조군이 보는 것은 `is_blocked` 뿐이다.
PASS_CONTROLS = (
    "관절이 약한 아이는 계단을 오르내리지 않게 하고, 미끄럼 방지 매트를 깔아 주세요.",
    "절뚝거림이 보이면 훈련 강도를 낮추고 평지에서 짧게 걷는 것부터 다시 시작하세요.",
    "산책 중 당김은 멈춤-보상으로 교정합니다. 줄이 느슨해지면 다시 출발하세요.",
)


def test_disease_plus_prescription_is_blocked():
    """병명과 처방 마커가 같이 오면 차단한다 — 전이 용어도 포함해서."""
    passed = [a for a in BLOCK_CONTROLS if not _verdict(a).is_blocked]
    assert not passed, f"처방을 담은 답변이 출력 게이트를 통과했다: {passed}"


def test_training_advice_with_clinical_words_is_not_blocked():
    """임상 낱말이 있어도 처방이 아니면 차단하지 않는다 — 오폭 방향."""
    blocked = {
        a: _verdict(a).matched_prescriptive_markers
        for a in PASS_CONTROLS
        if _verdict(a).is_blocked
    }
    assert not blocked, f"정상 훈련 조언이 출력 게이트에서 차단됐다: {blocked}"


def test_whitelist_does_not_survive_a_prescriptive_marker():
    """훈련 낱말이 있어도 처방 마커가 있으면 차단한다 (2026-08-25 규칙).

    Q&A 소스가 견주의 질문과 훈련사의 답을 한 검색에 담아, 답변이 한쪽의 훈련 어휘로 다른
    쪽의 약 어휘를 보증해 버리는 자리다. 규칙을 되돌리면 이 테스트가 깨진다.
    """
    answer = "분리불안이 심하면 진정제를 처방받아 복용시키면서 훈련하세요."
    verdict = _verdict(answer)
    assert verdict.whitelist_matched, "화이트리스트가 안 걸리면 이 테스트는 의미가 없다"
    assert verdict.is_blocked, "화이트리스트가 처방 마커를 무효화했다"


def test_system_authored_refusal_is_exempt():
    """시스템이 쓴 거절문은 출력 게이트를 통과해야 한다 — **사용자 노출 사고를 막는 자리다.**

    거절문 자신이 *"진단이나 처방을 할 수 없습니다"* 라고 말하므로 마커에 걸린다. 평문
    `str` 로 넘기면 차단되어, 사용자는 거절 안내 대신 차단 메시지를 받는다. 예외는 값의
    타입(`SystemAuthoredText`)에 걸려 있어서, 템플릿을 평범한 문자열로 바꾸는 순간 조용히
    무너진다. 그래서 **두 방향을 같이** 잠근다.
    """
    exempt = _verdict(MEDICAL_REFUSAL_TEMPLATE)
    assert not exempt.is_blocked, "시스템 거절문이 차단됐다 — 사용자가 거절 안내를 못 받는다"
    assert exempt.system_authored

    # 예외가 풀렸을 때 무슨 일이 나는지를 같이 못 박는다. 이쪽이 통과로 바뀌면 위 assert 가
    # 아무것도 안 지키고 있다는 뜻이다.
    assert _verdict(MEDICAL_REFUSAL_TEMPLATE.text).is_blocked, (
        "같은 글자를 평문으로 넣었는데 차단되지 않는다 — 예외가 지키는 것이 없다"
    )
