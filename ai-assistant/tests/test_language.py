from app.services.language import is_korean_dominant


def test_korean_sentence_is_dominant():
    assert is_korean_dominant("강아지는 하루에 두 번 밥을 먹어야 합니다.") is True


def test_english_sentence_is_not_dominant():
    assert is_korean_dominant("Dogs should eat twice a day.") is False


def test_mixed_with_majority_korean_is_dominant():
    assert is_korean_dominant("AAFCO 기준을 충족하는 사료를 고르는 것이 좋습니다.") is True


def test_mixed_with_majority_english_is_not_dominant():
    assert is_korean_dominant("This food meets 사료 기준.") is False


def test_numbers_and_symbols_only_pass_through():
    assert is_korean_dominant("123.456!!") is True


def test_empty_string_passes_through():
    assert is_korean_dominant("") is True


def test_korean_prefix_with_trailing_chinese_paragraph_is_not_dominant():
    """2026-08-20 실사례: 앞부분은 한국어인데 뒤 절반이 통째로 중국어로 바뀐 답변.
    전체 비율(0.516)만 보면 예전 threshold(0.5)를 근소하게 통과했었음 — 문장 단위
    검사가 이런 "부분 오염"을 잡아내는지 확인한다."""
    text = (
        "psonson(psonson은 한국의 수제간식 브랜드명으로, 여기서는 대표적인 소스테이크로 간주)"
        "를 주는 것은 좋지 않을 수 있습니다. 솜사탕은 주로 당분이 많이 들어 있어 과도한 섭취는 "
        "비만, 당뇨병, 치아 손상 등에 유발될 수 있습니다. 또한 과다한 섬유질은 장蠕动减慢"
        "也可能导致消化不良。因此，建议不要经常给狗狗喂食这类人类食品。最好还是遵循宠物食品的"
        "营养搭配，保证狗狗的健康。如果有任何疑问，最好咨询兽医以获取专业建议。"
    )
    assert is_korean_dominant(text) is False


def test_short_acronym_only_sentence_is_not_penalized():
    """짧은 문장(예: 약어 하나)은 min_sentence_len 미만이라 문장 단위 검사에서 건너뜀
    (전체 답변 안에 섞여 있고, 전체 비율은 여전히 threshold를 넘기는 경우)."""
    assert is_korean_dominant("체형 점수를 확인하세요. BCS. 이후 급여량을 조절합니다.") is True
