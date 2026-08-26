from app.services.guardrail import SAFE_FALLBACK_NOTICE, apply_guardrail


def test_apply_guardrail_removes_diagnostic_sentence():
    answer = "네, 알겠습니다. 이 증상은 파보바이러스 감염병일 수 있습니다. 충분한 휴식을 취하게 해주세요."

    result = apply_guardrail(answer)

    assert "병일 수 있습니다" not in result
    assert SAFE_FALLBACK_NOTICE in result


def test_apply_guardrail_removes_hospital_referral():
    answer = "포도는 위험합니다. 동물병원에 가보세요."

    result = apply_guardrail(answer)

    assert "동물병원에 가" not in result
    assert SAFE_FALLBACK_NOTICE in result


def test_apply_guardrail_passthrough_when_clean():
    answer = "포도는 강아지에게 위험한 음식입니다."

    result = apply_guardrail(answer)

    assert result == answer
