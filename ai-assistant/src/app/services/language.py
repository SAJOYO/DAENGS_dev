# language.py = 텍스트가 "한국어 위주"인지 판별하는 휴리스틱.
#
# 왜 필요한가?
#   로컬 생성 모델(qwen2.5:7b-instruct)이 간헐적으로 영어/중국어로 코드스위칭하는 현상이
#   관찰되었음 (services/guardrail.py 주석 참고). 이 함수는 그 문제를 잡아내기 위한 최소한의
#   검사기로, 질문(입력)과 답변(출력) 양쪽에 재사용됩니다.
#
# 왜 "전체 비율"만으로는 부족한가 (2026-08-20 실사례로 확인됨):
#   실제로 "솜사탕 먹여도 돼?"라는 (지식베이스에 없는) 질문에 모델이 앞부분은 한국어로 답하다가
#   뒷부분 절반 가까이를 통째로 중국어로 이어붙인 사례가 있었습니다. 이때 전체 글자 중 한글
#   비율은 0.516으로, 기존 threshold=0.5를 근소하게 넘겨 "한국어 위주"로 잘못 판정됐습니다
#   (앞쪽의 멀쩡한 한국어 문장들이 평균을 희석시킨 것). 그래서 전체 비율 검사에 더해,
#   "문장 하나가 통째로 다른 언어로 되어 있는지"를 문장 단위로도 검사합니다 — 이러면
#   AAFCO/RER/DER 같은 영단어 약어가 한두 개 섞인 정상적인 한국어 문장은 통과시키면서도,
#   한 문장 전체가 중국어/영어로 넘어가버린 경우는 잡아낼 수 있습니다.

import re

_HANGUL_PATTERN = re.compile(r"[가-힣]")
# \W는 "글자가 아닌 문자"(공백, 기호 등), \d는 숫자. [^\W\d_] = 그 둘과 밑줄을 뺀 "글자"만.
_LETTER_PATTERN = re.compile(r"[^\W\d_]", re.UNICODE)
# guardrail.py의 문장 분리 정규식과 동일한 패턴(마침표/느낌표/물음표 또는 한국어 어미 "다"/"요" 뒤 공백).
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?다요])\s+")


def _hangul_ratio(text: str) -> float | None:
    """text를 구성하는 "글자"(숫자/기호 제외) 중 한글 비율. 글자가 하나도 없으면 None."""
    letters = _LETTER_PATTERN.findall(text)
    if not letters:
        return None
    hangul_count = sum(1 for ch in letters if _HANGUL_PATTERN.match(ch))
    return hangul_count / len(letters)


def is_korean_dominant(
    text: str,
    threshold: float = 0.6,
    sentence_threshold: float = 0.25,
    min_sentence_len: int = 8,
) -> bool:
    """text가 한국어 위주인지 두 단계로 검사합니다.

    1) 전체 비율: 한글 비율이 threshold 이상이어야 통과.
    2) 문장 단위: 어느 정도 길이가 있는 문장(min_sentence_len자 이상 — "AAFCO"처럼 짧은
       약어 하나짜리 조각을 오탐하지 않기 위함) 중 한글 비율이 sentence_threshold 미만인
       문장이 하나라도 있으면 실패로 봄. 앞부분이 한국어라 전체 평균은 희석돼도, 문장
       하나가 통째로 다른 언어인 경우를 잡아내기 위한 2차 검사입니다.

    숫자/기호/이모지만 있고 "글자"가 하나도 없는 경우(예: "123", "🐶")는 판별할 언어
    자체가 없다고 보고 True(통과)로 처리합니다.
    """
    overall_ratio = _hangul_ratio(text)
    if overall_ratio is None:
        return True
    if overall_ratio < threshold:
        return False

    for sentence in _SENTENCE_BOUNDARY.split(text.strip()):
        sentence = sentence.strip()
        if len(sentence) < min_sentence_len:
            continue
        ratio = _hangul_ratio(sentence)
        if ratio is not None and ratio < sentence_threshold:
            return False

    return True
